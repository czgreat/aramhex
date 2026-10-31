"""装备、召唤师技能的小图标：按需从腾讯 CDN 下载到本地，界面用本地文件显示。

界面线程只查本地文件，不等网络：没有就先返回 None（界面先显示文字），同时交给后台线程下载；
下载完成后 version 加一，界面据此重新渲染。
本地没有的先从内置的 item_icons.json（1.1.2，scripts/update_static.py 生成）解出来，连不上腾讯也有图标；
解出来的文件日期记成过期，联网时照样换成最新的。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import http

log = logging.getLogger(__name__)
MAX_AGE_S = 14 * 86400  # 图标很少变；两周后顺手重下一次，跟上版本改图
BUNDLED = Path(__file__).with_name("item_icons.json")
_bundled: dict[str, dict[str, str]] | None = None


def bundled_icons(path: Path = BUNDLED) -> dict[str, dict[str, str]]:
    """内置图标 {kind: {key: PNG 的 base64}}；没有或坏了返回空。读一次就记住。"""
    global _bundled
    if _bundled is not None and path == BUNDLED:
        return _bundled
    try:
        d = json.loads(path.read_text(encoding="utf-8"))["icons"]
        out = {k: v for k, v in d.items() if isinstance(v, dict)}
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.info("内置图标读不到：%s", e)
        out = {}
    if path == BUNDLED:
        _bundled = out
    return out


class IconCache:
    def __init__(self, folder: Path, workers: int = 4, *, allow_network: bool = True):
        self.folder = folder
        self.version = 0
        self.allow_network = allow_network
        self._pending: set[str] = set()
        self._failed: dict[str, float] = {}
        # Only successfully decoded bundled keys enter this set (bounded by the
        # reviewed snapshot). Compare cached bytes once per process, not per paint.
        self._bundled_ready: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="icons")

    def local(self, kind: str, key: str, url: str | None) -> str | None:
        """本地图标路径（给 Qt 富文本 <img> 用），还没有就后台下载并返回 None。"""
        if not key:
            return None
        path = self.folder / kind / f"{key}.png"
        bundled_key = (kind, str(key))
        if not self.allow_network and bundled_key not in self._bundled_ready:
            changed = self._unpack(kind, str(key), path)
            if changed is not None:
                self._bundled_ready.add(bundled_key)
                if changed:
                    self.version += 1
        if not path.exists():
            self._unpack(kind, str(key), path)
        fresh = path.exists() and time.time() - path.stat().st_mtime < MAX_AGE_S
        if not fresh and url and self.allow_network:
            self._fetch(path, url)
        return path.as_posix() if path.exists() else None

    @staticmethod
    def _unpack(kind: str, key: str, path: Path) -> bool | None:
        blob = (bundled_icons().get(kind) or {}).get(key)
        if not blob:
            return None
        try:
            body = base64.b64decode(blob)
            if path.is_file() and path.read_bytes() == body:
                return False
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
            old = time.time() - MAX_AGE_S - 1
            os.utime(path, (old, old))  # 记成过期：能联网时照样下最新的
            return True
        except (OSError, ValueError) as e:
            log.info("内置图标解不出 %s/%s：%s", kind, key, e)
            return None

    def _fetch(self, path: Path, url: str) -> None:
        tag = str(path)
        with self._lock:
            if tag in self._pending or time.time() - self._failed.get(tag, 0) < 600:
                return
            self._pending.add(tag)
        self._pool.submit(self._download, path, url, tag)

    def _download(self, path: Path, url: str, tag: str) -> None:
        try:
            if url.startswith("//"):
                url = "https:" + url
            r = http._session.get(url, timeout=15)
            if r.status_code != 200 or not r.content.startswith(b"\x89PNG"):
                raise ValueError(f"HTTP {r.status_code}")
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(r.content)
            tmp.replace(path)
            with self._lock:
                self.version += 1
        except Exception as e:
            log.info("图标下载失败 %s：%s", url, e)
            with self._lock:
                self._failed[tag] = time.time()
        finally:
            with self._lock:
                self._pending.discard(tag)
