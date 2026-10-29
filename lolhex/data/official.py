"""已批准的官方资料整包：内置优先、后台更新、对局外原子切换。

此模块不采集腾讯资料，也没有第三方回源地址。调用方只在后台数据线程调用 sync。
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import math
import re
import shutil
import threading
import time
from dataclasses import dataclass
from contextlib import nullcontext
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

import requests

from .. import __version__, cloud

log = logging.getLogger(__name__)
SCHEMA_VERSION = 1
MAX_PACKAGE = 16 * 1024 * 1024
MAX_UNPACKED = 64 * 1024 * 1024
MAX_MANIFEST = 64 * 1024
STATIC = Path(__file__).with_name("static_bundled.json")
HEROES = Path(__file__).with_name("hero_bundled.json")


class OfficialDataError(ValueError):
    pass


def _version(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
        raise OfficialDataError("资料版本格式不对")
    parts = tuple(int(p) for p in value.split("."))
    return parts + (0,) * (4 - len(parts))


def _client_version(value: str) -> tuple[int, ...]:
    """客户端版本允许测试包后缀（例如 1.1.3-test.2），资料清单只比较正式版本号。"""
    if not isinstance(value, str):
        raise OfficialDataError("资料版本格式不对")
    match = re.fullmatch(r"(\d+(?:\.\d+){1,3})(?:-test\.\d+)?", value)
    if not match:
        raise OfficialDataError("资料版本格式不对")
    parts = tuple(int(p) for p in match.group(1).split("."))
    return parts + (0,) * (4 - len(parts))


def _date(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise OfficialDataError("资料日期格式不对")
    try:
        date.fromisoformat(value)
    except ValueError as e:
        raise OfficialDataError("资料日期无效") from e
    return value


def _integer(value, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise OfficialDataError(f"{name}无效")
    return value


def _relative_path(value) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise OfficialDataError("资料包路径无效")
    decoded = unquote(value)
    if (decoded != value or "\\" in value or any(c in value for c in ("?", "#", ":"))
            or value.startswith("/") or any(p in ("", ".", "..") for p in value.split("/"))):
        raise OfficialDataError("资料包必须使用安全相对路径")
    if not PurePosixPath(value).name.endswith(".json.gz"):
        raise OfficialDataError("资料包格式不支持")
    return value


def check_manifest(manifest: dict, client_version: str | None = None) -> dict:
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int \
            or manifest["schema_version"] != SCHEMA_VERSION:
        raise OfficialDataError("官方资料清单版本不支持")
    _integer(manifest.get("revision"), "资料序号", 1, 2**53 - 1)
    _version(manifest.get("patch"))
    _date(manifest.get("source_date"))
    try:
        generated = datetime.fromisoformat(manifest["generated_at"].replace("Z", "+00:00"))
        if generated.tzinfo is None:
            raise ValueError("timezone missing")
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        raise OfficialDataError("资料生成时间无效") from e
    minimum = _version(manifest.get("min_client"))
    if client_version is not None and minimum > _client_version(client_version):
        raise OfficialDataError("官方资料需要较新的程序版本")
    package = manifest.get("package")
    if not isinstance(package, dict):
        raise OfficialDataError("官方资料清单缺包信息")
    _relative_path(package.get("path"))
    _integer(package.get("size"), "资料包大小", 1, MAX_PACKAGE)
    _integer(package.get("unpacked_size"), "解压大小", 1, MAX_UNPACKED)
    if not isinstance(package.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", package["sha256"]):
        raise OfficialDataError("资料包缺 SHA-256")
    return manifest


def check_payload(payload: dict, manifest: dict | None = None) -> dict:
    # SHA/长度只证明传输完整；还要在替换可用快照之前校验 UI/识别实际读取的形状。
    # 未知附加字段保留，已知的可选字段允许缺省，不把未来扩展当作坏包。
    def text(value, label, *, required=False):
        if value is None and not required:
            return
        if not isinstance(value, str) or (required and not value.strip()):
            raise OfficialDataError(f"{label}不是有效文本")

    def identifier(value, label):
        if not isinstance(value, str) or not value.isdigit():
            raise OfficialDataError(f"{label}不是有效 ID")

    def rows(value, label):
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise OfficialDataError(f"{label}不是对象列表")
        return value

    def strings(value, label):
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
            raise OfficialDataError(f"{label}不是文本列表")

    def stats(row, label):
        for key in ("win", "pick", "games", "rank", "overall_rank"):
            value = row.get(key)
            if value is None:
                continue
            if (type(value) not in (int, float) or not 0 <= value <= 2**53 - 1
                    or (isinstance(value, float) and not math.isfinite(value))
                    or (key in ("win", "pick") and value > 1)):
                raise OfficialDataError(f"{label}.{key}不是有效统计数值")

    def named_fields(row, label):
        for key in ("name", "icon", "desc", "en", "keywords"):
            text(row.get(key), f"{label}.{key}")

    if not isinstance(payload, dict):
        raise OfficialDataError("官方资料包不是对象")
    static, heroes = payload.get("static"), payload.get("heroes")
    if not isinstance(static, dict) or not isinstance(heroes, dict):
        raise OfficialDataError("官方资料包缺基础或英雄资料")
    patch = static.get("patch")
    _version(patch)
    source_date = _date(static.get("generated"))
    if heroes.get("patch") != patch or heroes.get("generated") != source_date:
        raise OfficialDataError("基础与英雄资料的版本或日期不一致")
    base, details = static.get("heroes"), heroes.get("heroes")
    if not isinstance(base, dict) or not base or not isinstance(details, dict) or set(base) != set(details):
        raise OfficialDataError("官方资料没有覆盖完整英雄集合")
    for hid, info in base.items():
        if not isinstance(hid, str) or not hid.isdigit() or not isinstance(info, dict):
            raise OfficialDataError("英雄字典内容无效")
        if info.get("id") != hid:
            raise OfficialDataError("英雄 ID 与字典键不一致")
        for key in ("name", "nick", "alias"):
            text(info.get(key), f"英雄.{key}", required=True)
        named_fields(info, "英雄")
        d = details[hid]
        if not isinstance(d, dict) or d.get("patch") != patch:
            raise OfficialDataError("单英雄资料版本不一致")
        if str(d.get("date", "")) != source_date.replace("-", ""):
            raise OfficialDataError("单英雄资料来源日期不一致")
        if not isinstance(d.get("augments"), list) or not isinstance(d.get("builds"), dict):
            raise OfficialDataError("单英雄资料缺海克斯或出装")
        for row in rows(d["augments"], "英雄海克斯"):
            identifier(row.get("id"), "英雄海克斯.id")
            for key in ("rarity", "tier", "overall_tier"):
                text(row.get(key), f"英雄海克斯.{key}")
            stats(row, "英雄海克斯")
        for key in ("full", "core", "first", "shoes", "start"):
            for row in rows(d["builds"].get(key, []), f"出装.{key}"):
                if key in ("first", "shoes"):
                    identifier(row.get("item"), f"出装.{key}.item")
                else:
                    strings(row.get("items"), f"出装.{key}.items")
                    for item in row["items"]:
                        identifier(item, f"出装.{key}.items")
                stats(row, f"出装.{key}")
        for row in rows(d.get("skills", []), "加点"):
            strings(row.get("max", []), "加点.max")
            stats(row, "加点")
            for sequence in rows(row.get("sequences", []), "逐级加点"):
                strings(sequence.get("seq", []), "逐级加点.seq")
                stats(sequence, "逐级加点")
        for row in rows(d.get("partners", []), "搭档"):
            identifier(row.get("hero"), "搭档.hero")
            stats(row, "搭档")
        spells = d.get("spells", {})
        if not isinstance(spells, dict) or any(not isinstance(row, dict) for row in spells.values()):
            raise OfficialDataError("英雄技能不是对象字典")
        for row in spells.values():
            named_fields(row, "英雄技能")
        text(d.get("splash"), "英雄原画")
    for key in ("augments", "items", "spells"):
        if not isinstance(static.get(key), dict) or not static[key]:
            raise OfficialDataError(f"基础资料缺{key}")
        for key_id, row in static[key].items():
            identifier(key_id, f"基础资料.{key}")
            if not isinstance(row, dict):
                raise OfficialDataError(f"基础资料.{key}含非对象条目")
            named_fields(row, f"基础资料.{key}")
            if "id" in row and row["id"] != key_id:
                raise OfficialDataError(f"基础资料.{key}的 ID 与字典键不一致")
            if key == "augments":
                identifier(row.get("id"), "基础海克斯.id")
                for field in ("name", "rarity"):
                    text(row.get(field), f"基础海克斯.{field}", required=True)
                strings(row.get("modes", []), "基础海克斯.modes")
    if manifest is not None and (patch != manifest.get("patch") or source_date != manifest.get("source_date")):
        raise OfficialDataError("包内容与清单版本或日期不一致")
    return payload


def _json(raw: bytes) -> dict:
    def bad_constant(value):
        raise OfficialDataError(f"JSON 含无效数值 {value}")
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=bad_constant)
    except (ValueError, UnicodeError) as e:
        raise OfficialDataError("官方资料 JSON 无效") from e
    if not isinstance(value, dict):
        raise OfficialDataError("官方资料 JSON 不是对象")
    return value


def _unpack(raw: bytes, manifest: dict) -> dict:
    package = manifest["package"]
    if len(raw) != package["size"] or hashlib.sha256(raw).hexdigest() != package["sha256"]:
        raise OfficialDataError("官方资料包长度或 SHA-256 不符")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            decoded = stream.read(package["unpacked_size"] + 1)
    except (OSError, EOFError) as e:
        raise OfficialDataError("官方资料包解压失败") from e
    if len(decoded) != package["unpacked_size"]:
        raise OfficialDataError("官方资料解压大小不符")
    return check_payload(_json(decoded), manifest)


def _atomic_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


@dataclass(frozen=True)
class OfficialSnapshot:
    static: dict
    heroes: dict
    manifest: dict
    origin: str

    @property
    def revision(self) -> int:
        return self.manifest["revision"]


class ManagedOfficial:
    """root 为独立 official 目录；构造只读本地，sync 只应由后台线程调用。"""
    def __init__(self, root: Path, static_path: Path = STATIC, heroes_path: Path = HEROES, *,
                 minutes=None, sources=None, hold=None, clock=time.time, session=None,
                 allow_http_local: bool = False):
        self.root = Path(root)
        self.minutes = minutes or cloud.official_minutes
        self.sources = sources or (lambda: cloud.mirrors("official"))
        self.hold = hold or (lambda: False)
        self.clock = clock
        self.session = session or requests.Session()
        self.allow_http_local = allow_http_local
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._last_check: float | None = None
        self._last_sources: tuple[str, ...] | None = None
        self._retry_at: float | None = None
        self._pending: tuple[str, OfficialSnapshot] | None = None
        self.activation_guard = nullcontext
        payload = check_payload({"static": _json(Path(static_path).read_bytes()),
                                 "heroes": _json(Path(heroes_path).read_bytes())})
        self._bundled_hero_ids = frozenset(payload["heroes"]["heroes"])
        self._bundled_floor = (_version(payload["static"]["patch"]), payload["static"]["generated"])
        self.snapshot = OfficialSnapshot(payload["static"], payload["heroes"]["heroes"],
                                         {"revision": 0, "patch": payload["static"]["patch"],
                                          "source_date": payload["static"]["generated"]}, "bundled")
        active = self._load_pointer("active.json")
        if active:
            self.snapshot = active[1]
        self._pending = self._load_pointer("pending.json")
        self._load_check_state()

    def _load_check_state(self) -> None:
        path = self.root / "check-state.json"
        if not path.exists():
            return
        try:
            state = _json(path.read_bytes())
            last, sources = state.get("last_check"), state.get("sources")
            if (isinstance(last, (int, float)) and not isinstance(last, bool) and math.isfinite(last)
                    and last >= 0 and isinstance(sources, list) and len(sources) <= 5
                    and all(isinstance(s, str) and len(s) <= 2048 for s in sources)):
                self._last_check, self._last_sources = float(last), tuple(sources)
        except (OSError, ValueError):
            pass  # 检查时间坏了只会重新查清单，不影响本地资料。

    def _load_pointer(self, name: str) -> tuple[str, OfficialSnapshot] | None:
        pointer = self.root / name
        if not pointer.exists():
            return None
        try:
            folder = _json(pointer.read_bytes()).get("folder")
            if not isinstance(folder, str) or not re.fullmatch(r"\d+-[a-f0-9]{12}", folder):
                raise OfficialDataError("本地官方资料指针无效")
            dest = self.root / "revisions" / folder
            manifest = check_manifest(_json((dest / "manifest.json").read_bytes()), __version__)
            payload = _unpack((dest / "package.gz").read_bytes(), manifest)
            self._check_hero_coverage(payload)
            snapshot = self._snapshot(payload, manifest)
            if (_version(manifest["patch"]) < self._bundled_floor[0]
                    or manifest["source_date"] < self._bundled_floor[1]):
                return None  # 安装较新程序后，不让旧下载资料覆盖内置资料。
            return folder, snapshot
        except (OSError, ValueError, TypeError) as e:
            self.last_error = str(e)
            log.warning("忽略损坏的本地官方资料：%s", e)
            return None

    @staticmethod
    def _snapshot(payload: dict, manifest: dict) -> OfficialSnapshot:
        return OfficialSnapshot(payload["static"], payload["heroes"]["heroes"], manifest, "downloaded")

    def _check_hero_coverage(self, payload: dict) -> None:
        required = self._bundled_hero_ids | set(self.snapshot.heroes)
        if not required.issubset(payload["heroes"]["heroes"]):
            raise OfficialDataError("官方资料遗漏内置或当前使用的英雄")

    def _config(self) -> tuple[int, tuple[str, ...]]:
        raw = self.minutes()
        minutes = int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw) else 0
        minutes = max(0, min(43200, minutes))
        urls = []
        for value in self.sources() or []:
            if not isinstance(value, str):
                continue
            try:
                u = urlsplit(value)
            except ValueError:
                continue
            local = self.allow_http_local and u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1")
            host = u.hostname or ""
            third_party = any(host == suffix or host.endswith("." + suffix)
                              for suffix in ("qq.com", "gtimg.cn", "gtimg.com", "communitydragon.org"))
            if (u.scheme == "https" or local) and host and not third_party and not u.username and not u.password \
                    and not u.query and not u.fragment:
                urls.append(value.rstrip("/"))
        return minutes, tuple(urls[:5])

    def seconds_until_check(self) -> float | None:
        minutes, sources = self._config()
        if not minutes or not sources:
            return None
        if sources != self._last_sources:
            return 0.0
        now = self.clock()
        if self._retry_at is not None:
            return max(0.0, self._retry_at - now)
        if self._last_check is None:
            return 0.0
        if now < self._last_check:
            return 0.0  # 系统时钟回拨后重新查一次，不能无限等未来时间。
        return max(0.0, self._last_check + minutes * 60 - now)

    def _get(self, url: str, maximum: int) -> bytes:
        response = self.session.get(url, timeout=(5, 15), stream=True, allow_redirects=False,
                                    headers=cloud.headers(url))
        try:
            if response.status_code != 200:
                raise OfficialDataError(f"官方资料 HTTP {response.status_code}")
            chunks, count = [], 0
            for chunk in response.iter_content(64 * 1024):
                count += len(chunk)
                if count > maximum:
                    raise OfficialDataError("官方资料响应超过大小上限")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            response.close()

    def _newer(self, manifest: dict) -> bool:
        if (_version(manifest["patch"]) < self._bundled_floor[0]
                or manifest["source_date"] < self._bundled_floor[1]):
            return False
        current = self.snapshot.manifest
        return (manifest["revision"] > current["revision"]
                and _version(manifest["patch"]) >= _version(current["patch"])
                and manifest["source_date"] >= current["source_date"])

    def _activate(self) -> bool:
        with self.activation_guard():
            return self._activate_guarded()

    def _activate_guarded(self) -> bool:
        if self._pending is None or self.hold():
            return False
        folder, snapshot = self._pending
        if not self._newer(snapshot.manifest):
            self._pending = None
            return False
        _atomic_json(self.root / "active.json", {"folder": folder})
        self.snapshot = snapshot
        self._pending = None
        log.info("官方资料已切换：版本=%s 第%d版 来源=%s", snapshot.manifest["patch"],
                 snapshot.revision, snapshot.origin)
        pending = self.root / "pending.json"
        try:
            pending.unlink(missing_ok=True)
        except OSError:
            # active.json 已完成切换；清理失败不能让调用方错以为没有新快照。
            log.info("官方资料已启用，待更新指针暂未清理")
        self._prune_revisions(keep=folder)
        return True

    def _prune_revisions(self, keep: str) -> None:
        """换上新一版后删掉旧版本目录：每版整包约 0.5 MB，不删就每次更新都多留一份。"""
        try:
            old = [d for d in (self.root / "revisions").iterdir() if d.is_dir() and d.name != keep]
        except OSError:
            return
        for d in old:
            shutil.rmtree(d, ignore_errors=True)

    def activate_pending(self) -> bool:
        with self._lock:
            return self._activate()

    def _record_check(self, checked_at: float, sources: tuple[str, ...]) -> None:
        self._last_check, self._last_sources = checked_at, sources
        self._retry_at = None
        try:
            _atomic_json(self.root / "check-state.json", {"last_check": checked_at,
                                                          "sources": list(sources)})
        except OSError:
            log.info("官方资料检查时间暂未保存")

    def sync(self, force: bool = False) -> bool:
        """有新版才下载；返回 True 表示可安全切换的快照已激活。"""
        if not self._lock.acquire(blocking=False):
            return False
        changed = False
        try:
            changed = self._activate()
            due = self.seconds_until_check()
            if due is None or (not force and due > 0):
                return changed
            minutes, sources = self._config()
            if not minutes or not sources:
                return changed
            checked_at = self.clock()
            for base in sources:
                try:
                    manifest = check_manifest(_json(self._get(base + "/latest.json", MAX_MANIFEST)), __version__)
                    if not self._newer(manifest):
                        self._record_check(checked_at, sources)
                        self.last_error = None
                        return changed
                    if self._pending and self._pending[1].manifest == manifest:
                        self._record_check(checked_at, sources)
                        return changed or self._activate()
                    raw = self._get(base + "/" + manifest["package"]["path"], manifest["package"]["size"])
                    payload = _unpack(raw, manifest)
                    self._check_hero_coverage(payload)
                    folder = f"{manifest['revision']}-{manifest['package']['sha256'][:12]}"
                    dest = self.root / "revisions" / folder
                    dest.mkdir(parents=True, exist_ok=True)
                    temporary = dest / "package.tmp"
                    temporary.write_bytes(raw)
                    temporary.replace(dest / "package.gz")
                    _atomic_json(dest / "manifest.json", manifest)
                    _atomic_json(self.root / "pending.json", {"folder": folder})
                    self._pending = folder, self._snapshot(payload, manifest)
                    self._record_check(checked_at, sources)
                    self.last_error = None
                    return self._activate() or changed
                except (OSError, ValueError, TypeError, requests.RequestException) as e:
                    self.last_error = str(e)
                    log.info("官方资料更新失败，继续使用本地资料：%s", e)
            # 网络或资料格式失败时不要把持久检查时间写成“成功”；但也不能每秒重试。
            self._last_sources = sources
            self._retry_at = checked_at + min(minutes, 10) * 60
            return changed
        except (OSError, ValueError, TypeError) as e:
            self.last_error = str(e)
            self._last_sources = sources if 'sources' in locals() else self._last_sources
            self._retry_at = self.clock() + min(minutes, 10) * 60 if 'minutes' in locals() else None
            return changed
        finally:
            self._lock.release()

    def status(self) -> dict:
        snapshot, pending = self.snapshot, self._pending
        return {"official_revision": snapshot.revision, "official_origin": snapshot.origin,
                "official_date": snapshot.manifest["source_date"],
                "official_pending": pending[1].revision if pending else None,
                "official_error": self.last_error}
