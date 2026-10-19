"""云控配置：aramhex.com/config.json（网站和软件共用，改了不用发版）。

    downloads   网盘链接 {setup, portable}（每次发版会换）
    community   社区入口列表 [{name, text, url, qr, primary}]：QQ 群、B 站、赞助……
                软件「关于」里全部列出；primary 的那条在主界面顶部显示成按钮（网站同理）
    notice      公告 {id, text, url}：同一个 id 只弹一次
    min_version 低于这个版本必须更新
    rank        查分 {enabled, api, message}：可以远程关掉查询
    event       当前活动 {mode, label, start, end}：例如 first-prismatic「首次必棱彩」，活动期间改用这个规则的统计数据
    mirrors     额外线路 {update: [".../{version}"], data: [...]}：换服务器、加线路不用发版，排在内置线路前面
    features    功能开关（预留）：{名字: true/false}
    update      检查频率和分批推送（1.1.0 起）：
                  check_minutes   多久查一次云控和新版本（默认 180，30–1440）；启动时、手动「检查更新」照常查
                  data_minutes    多久查一次统计数据（默认 360，60–1440）
                  rollout_version / rollout_percent  这个版本先只提示一部分人（0–100）。按本机随机编号分组，
                                  同一台电脑分组固定，把比例调大，之前提示过的人仍在里面
                  paused          true 时暂停提示新版本（发现问题时用）；「最低版本」和手动检查不受影响

连不上就用上次存下的（app_home/cloud.json），再没有就用内置默认值。
"""

from __future__ import annotations

import json
import logging
import threading

import requests

from . import WEBSITE, __version__
from .config import app_home

log = logging.getLogger(__name__)
URL = f"{WEBSITE}/config.json"
DEFAULT = {
    "downloads": {},
    "community": [],
    "notice": {},
    "min_version": "",
    "rank": {"enabled": True, "api": f"{WEBSITE}/api/rank", "message": ""},
    "event": {"mode": "", "label": "", "start": "", "end": ""},
    "mirrors": {"update": [], "data": []},
    "features": {},
    "update": {"check_minutes": 180, "data_minutes": 360, "rollout_version": "", "rollout_percent": 100,
               "paused": False},
}
_lock = threading.Lock()
_cache: dict | None = None
_install_id: str | None = None  # 匿名使用统计的编号；关掉时为 None，不带


def set_install_id(value: str | None) -> None:
    global _install_id
    _install_id = value or None


def _path():
    return app_home() / "cloud.json"


def _merge(raw: dict) -> dict:
    out = json.loads(json.dumps(DEFAULT))
    for k, v in (raw or {}).items():
        if k == "community" and isinstance(v, list):
            items = []
            for it in v[:8]:
                if not isinstance(it, dict) or not str(it.get("name") or "").strip():
                    continue
                url = str(it.get("url") or "")
                qr = str(it.get("qr") or "")
                items.append({"name": str(it["name"]).strip()[:20], "text": str(it.get("text") or "").strip()[:40],
                              "url": url if url.startswith(("https://", "http://")) else "",
                              "qr": qr if qr.startswith("https://") else "", "primary": bool(it.get("primary"))})
            out[k] = items
        elif k == "mirrors" and isinstance(v, dict):
            for kk in ("update", "data"):
                urls = v.get(kk)
                if isinstance(urls, list):
                    out[k][kk] = [u for u in urls if isinstance(u, str) and u.startswith("https://")][:5]
        elif k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k].update({kk: vv for kk, vv in v.items() if isinstance(vv, (str, bool, int, float))})
        elif k in out and not isinstance(out[k], dict) and isinstance(v, (str, int, float)):
            out[k] = str(v)
    return out


def get() -> dict:
    """当前配置（不联网）。"""
    global _cache
    with _lock:
        if _cache is None:
            try:
                _cache = _merge(json.loads(_path().read_text(encoding="utf-8")))
            except (OSError, ValueError):
                _cache = _merge({})
        return _cache


def mirrors(kind: str) -> list[str]:
    """云控里的额外线路（update 里的 {version} 由调用方替换）。"""
    return list((get().get("mirrors") or {}).get(kind) or [])


def community() -> list[dict]:
    return list(get().get("community") or [])


def primary_link() -> dict | None:
    """主界面顶部按钮：第一条标了 primary 且有链接的。"""
    return next((c for c in community() if c.get("primary") and c.get("url")), None)


def event() -> dict | None:
    """正在进行的活动 {mode, label}；没有返回 None。start / end 是带时区的 ISO 时间，空着表示不限。"""
    from datetime import datetime, timezone
    ev = get().get("event") or {}
    mode = str(ev.get("mode") or "").strip()
    if not mode or mode == "normal":
        return None
    now = datetime.now(timezone.utc)
    try:
        if ev.get("start") and now < datetime.fromisoformat(ev["start"]):
            return None
        if ev.get("end") and now >= datetime.fromisoformat(ev["end"]):
            return None
    except ValueError:
        return None
    return {"mode": mode, "label": str(ev.get("label") or mode)}


def _minutes(key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(float((get().get("update") or {}).get(key, default)))
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def check_minutes() -> int:
    """多久查一次云控和新版本（分钟）。"""
    return _minutes("check_minutes", 180, 30, 1440)


def data_minutes() -> int:
    """多久查一次统计数据（分钟）。"""
    return _minutes("data_minutes", 360, 60, 1440)


def bucket(install_id: str | None) -> int:
    """本机分组 0–99：按安装时生成的随机编号算，只在本机用。没有编号的算最后一组。"""
    import hashlib
    if not install_id:
        return 99
    return int(hashlib.sha256(install_id.encode()).hexdigest()[:8], 16) % 100


def offer_update(version: str, install_id: str | None) -> bool:
    """定时检查查到新版本后，要不要提示这台电脑（分批推送、暂停）。"""
    u = get().get("update") or {}
    if u.get("paused"):
        return False
    if str(u.get("rollout_version") or "").lstrip("vV") != str(version).lstrip("vV"):
        return True
    try:
        pct = int(float(u.get("rollout_percent", 100)))
    except (TypeError, ValueError):
        pct = 100
    return bucket(install_id) < max(0, min(100, pct))


def feature(name: str, default: bool = True) -> bool:
    v = (get().get("features") or {}).get(name)
    return default if v is None else bool(v)


def refresh(timeout: float = 6, url: str = URL) -> dict:
    """联网取最新配置并存下来；失败就返回上次的。"""
    global _cache
    try:
        headers = {"User-Agent": f"aramhex/{__version__}"}
        if _install_id:
            headers["X-AramHex-Id"] = _install_id  # 只用来数「同一台电脑是不是一直在用」
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        raw = r.json()
        if not isinstance(raw, dict):
            raise ValueError("格式不对")
    except (requests.RequestException, ValueError) as e:
        log.info("取云控配置失败：%s", e)
        return get()
    cfg = _merge(raw)
    with _lock:
        _cache = cfg
    try:
        _path().write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return cfg
