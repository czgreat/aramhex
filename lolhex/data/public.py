"""自采统计数据（公开数据仓库 github.com/czgreat/aramhex-data 的格式）。

一份数据 = 一个目录：
    latest.json                      清单：版本、样本时间、文件列表（SHA-256、大小）
    meta.json                        口径、样本时间窗、局限
    heroes.json / hero_augments.json 全体
    bands/<casual|mid|high|top>/heroes.json、hero_augments.json   分段（可选）

两个来源，用样本更新的那份：
- 安装包内置：lolhex/data/bundled/（发版时由 scripts/update_data.py 放进来）；
- 下载的：<app_home>/data/public/（启动时查数据仓库的 latest.json，有新数据就下载，逐个文件校验 SHA-256）。
  先试 GitHub，连不上或校验不过就换 jsDelivr 镜像（DATA_SOURCES）。

官方资料（英雄、海克斯字典、出装、加点）仍由父类 DataStore 直接从腾讯取。
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import time
import math
from pathlib import Path

import requests

from .. import DATA_REPO, MIRROR, cloud
from . import http
from .store import DataStore, _read

log = logging.getLogger(__name__)

# 1.1.1 起只分三档（数据里 high、top 早就合成了「前 5%」，bands/top 和 bands/high 内容相同）
BANDS = {"casual": "娱乐", "mid": "中分", "high": "前 5%"}
BAND_ALIASES = {"top": "high"}  # 老设置文件、查分服务里的 top 当 high


def norm_band(band):
    return BAND_ALIASES.get(band, band) if band else band
BUNDLED_DIR = Path(__file__).with_name("bundled")
RAW_BASE = f"https://raw.githubusercontent.com/{DATA_REPO}/main"
# 先走官网镜像（DMIT 定时从 GitHub 同步），不通再试 GitHub 和 jsDelivr。每个文件都按清单校验 SHA-256。
DATA_SOURCES = [f"{MIRROR}/data", RAW_BASE] + [f"https://{host}/gh/{DATA_REPO}@main"
                             for host in ("fastly.jsdelivr.net", "gcore.jsdelivr.net", "cdn.jsdelivr.net")]
# jsDelivr 按分支（@main）取会缓存好几个小时；数据每更新一次打 tag data-v<序号>，按"下一个序号"试探，按 tag 取是即时的
DATA_TAG_HOSTS = ("fastly.jsdelivr.net", "gcore.jsdelivr.net")
DATA_TAG_FMT = "https://{host}/gh/" + DATA_REPO + "@data-v{seq}"
SYNC_EVERY_S = 6 * 3600
SCHEMA_VERSION = 1


class DatasetError(ValueError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_manifest(manifest: dict) -> dict:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise DatasetError("数据清单版本不支持")
    files = manifest.get("files")
    if not isinstance(files, dict) or "meta.json" not in files or "heroes.json" not in files \
            or "hero_augments.json" not in files:
        raise DatasetError("数据清单缺文件")
    for name, f in files.items():
        if ".." in name or name.startswith(("/", "\\")) or not name.endswith(".json"):
            raise DatasetError(f"数据清单里有不合法的文件名：{name}")
        if not isinstance(f, dict) or len(str(f.get("sha256", ""))) != 64:
            raise DatasetError(f"数据清单里 {name} 没有 SHA-256")
    return manifest


def _required_number(value, *, integer: bool = False, minimum=None, maximum=None) -> bool:
    if integer and type(value) is not int:
        return False
    if not integer and (type(value) not in (int, float) or isinstance(value, bool)):
        return False
    if not math.isfinite(value):
        return False
    return ((minimum is None or value >= minimum) and
            (maximum is None or value <= maximum))


def _validate_table_shape(name: str, value, *, augment: bool = False) -> None:
    if not isinstance(value, dict) or not value:
        raise DatasetError(f"{name} 顶层必须是对象")
    for hid, row in value.items():
        if not isinstance(hid, str) or not hid.isdigit() or not isinstance(row, dict):
            raise DatasetError(f"{name} 有无效英雄记录")
        if augment:
            for aid, stats in row.items():
                if not isinstance(aid, str) or not aid.isdigit() or not isinstance(stats, dict):
                    raise DatasetError(f"{name} 有无效海克斯记录")
                _validate_stats(name, stats)
        else:
            _validate_stats(name, row, require_win_rate=True)


def _validate_stats(name: str, stats: dict, *, require_win_rate: bool = True) -> None:
    required = ("games", "wins") + (("win_rate",) if require_win_rate else ())
    if any(k not in stats for k in required):
        raise DatasetError(f"{name} 缺少统计字段")
    if not _required_number(stats["games"], integer=True, minimum=1):
        raise DatasetError(f"{name} 局数字段无效")
    if not _required_number(stats["wins"], integer=True, minimum=0, maximum=stats["games"]):
        raise DatasetError(f"{name} 胜场字段无效")
    if require_win_rate and not _required_number(stats["win_rate"], minimum=0, maximum=1):
        raise DatasetError(f"{name} 胜率字段无效")


def _validate_dataset_shape(folder: Path, manifest: dict) -> None:
    """校验下载资料的实际消费形状，避免正确 SHA 的坏 JSON 被激活。"""
    meta = _read(folder / "meta.json")
    if not isinstance(meta, dict):
        raise DatasetError("meta.json 顶层必须是对象")
    for key in ("provisional_augments", "removed_augments", "excluded_augments"):
        if key in meta and not isinstance(meta[key], dict):
            raise DatasetError(f"meta.json 的 {key} 必须是对象")
    _validate_table_shape("heroes.json", _read(folder / "heroes.json"))
    _validate_table_shape("hero_augments.json", _read(folder / "hero_augments.json"), augment=True)
    band_files = {name for name in manifest["files"] if name.startswith("bands/")}
    expected_bands = {f"bands/{band}/{kind}.json" for band in (*BANDS, "top")
                      for kind in ("heroes", "hero_augments")}
    if manifest.get("bands") is True and not expected_bands <= set(manifest["files"]):
        raise DatasetError("分段数据缺少完整表")
    if manifest.get("bands") is not True and band_files:
        raise DatasetError("清单声明无分段但包含分段数据")
    for name in manifest["files"]:
        if name.startswith("bands/") and name.endswith("/heroes.json"):
            _validate_table_shape(name, _read(folder / name))
        elif name.startswith("bands/") and name.endswith("/hero_augments.json"):
            _validate_table_shape(name, _read(folder / name), augment=True)
        elif name.rsplit("/", 1)[-1] in ("hero_spells.json", "hero_items.json"):
            # 可选表（含分段里的）只校验到「英雄 → 对象」这一层；每行坏数据由面板过滤，保留能用的行
            value = _read(folder / name)
            if not isinstance(value, dict):
                raise DatasetError(f"{name} 顶层必须是对象")
            for hid, rows in value.items():
                if not isinstance(hid, str) or not hid.isdigit() or not isinstance(rows, dict):
                    raise DatasetError(f"{name} 有无效英雄记录")


class Dataset:
    """读进内存的一份数据。构造时逐个文件核对 SHA-256，不对就整份不用。"""

    def __init__(self, folder: Path, origin: str):
        self.folder, self.origin = Path(folder), origin
        manifest = _read(self.folder / "latest.json")
        if manifest is None:
            raise DatasetError(f"{folder} 没有 latest.json")
        self.manifest = check_manifest(manifest)
        for name, f in self.manifest["files"].items():
            p = self.folder / name
            if not p.is_file() or _sha256(p) != f["sha256"]:
                raise DatasetError(f"{name} 缺失或校验不对")
        _validate_dataset_shape(self.folder, self.manifest)
        self.meta = _read(self.folder / "meta.json") or {}
        self.patch = str(self.manifest.get("patch") or self.meta.get("patch") or "")
        self.snapshot_at = str(self.manifest.get("snapshot_at") or "")
        self.seq = int(self.manifest.get("seq") or 0)  # 数据更新序号，对应 tag data-v<seq>
        self.mode = str(self.manifest.get("mode") or "normal")       # normal / first-prismatic 等
        self.mode_label = str(self.manifest.get("mode_label") or "")
        # 数据版本号：整份清单（全部文件的 SHA-256 + 样本时间）的摘要，任何一个文件变了都会变
        ident = json.dumps([self.patch, self.snapshot_at, self.manifest["files"]], sort_keys=True)
        self.revision = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:12]
        self._tables: dict[str, tuple[dict, dict]] = {}
        self._shrink: dict[str, int] = {}

    def bands_available(self) -> bool:
        return all(f"bands/{b}/heroes.json" in self.manifest["files"] for b in BANDS)

    def band_shrink(self, band: str) -> int:
        if band not in self._shrink:
            self._shrink[band] = _band_shrink(self.table(None), self.table(band))
        return self._shrink[band]

    def optional_table(self, name: str, band: str | None = None) -> dict:
        """可选的数据文件（hero_spells.json、hero_items.json，1.1.1 起读）：清单里有就读，没有返回 {}。
        选了分段且有分段文件时读分段的。老版本客户端不认这些文件，忽略。"""
        for prefix in ((f"bands/{band}/",) if band else ()) + ("",):
            if prefix + name in self.manifest["files"]:
                key = "opt:" + prefix + name
                if key not in self._tables:
                    data = _read(self.folder / (prefix + name))
                    self._tables[key] = data if isinstance(data, dict) else {}
                return self._tables[key]
        return {}

    def table(self, band: str | None) -> tuple[dict, dict]:
        """(heroes, hero_augments)；band=None 为全体。"""
        key = band or ""
        if key not in self._tables:
            prefix = f"bands/{band}/" if band else ""
            self._tables[key] = (_read(self.folder / f"{prefix}heroes.json") or {},
                                 _read(self.folder / f"{prefix}hero_augments.json") or {})
        return self._tables[key]


SHRINK_DEFAULT, SHRINK_MIN, SHRINK_MAX = 3000, 1000, 30000


def _band_shrink(tables_all: tuple[dict, dict], tables_band: tuple[dict, dict]) -> int:
    """分段和全体综合时的 K（局数）：分段里有 K 局时，「这个海克斯比英雄平均高多少」分段和全体各信一半（Gitea #7）。
    按这份数据自己估：同英雄同海克斯，分段的优势减全体的优势，方差扣掉随机误差就是真实差别 τ²，K = 0.25 / τ²。
    16.20 估出来：娱乐、中分约 1.1 万，前 5% 约 3400。格子太少（新版本刚开始）用默认值。"""
    heroes_all, augs_all = tables_all
    heroes_b, augs_b = tables_band
    sq = noise = 0.0
    n = 0
    for hid, augs in augs_b.items():
        hb, ha = heroes_b.get(hid), heroes_all.get(hid)
        if not hb or not ha:
            continue
        for aid, x in augs.items():
            y = (augs_all.get(hid) or {}).get(aid)
            if not y or x.get("games", 0) < 1000 or not y.get("games"):
                continue
            d = (x["win_rate"] - hb["win_rate"]) - (y["win_rate"] - ha["win_rate"])
            p = x["win_rate"]
            sq += d * d
            noise += p * (1 - p) * max(1 / x["games"] - 1 / y["games"], 1 / y["games"])
            n += 1
    if n < 200:
        return SHRINK_DEFAULT
    tau2 = sq / n - noise / n
    if tau2 <= 0:
        return SHRINK_MAX
    return int(min(SHRINK_MAX, max(SHRINK_MIN, 0.25 / tau2)))


def load_best(folders: list[tuple[Path, str]]) -> Dataset | None:
    """在几个候选目录里挑样本最新、校验通过的一份。"""
    best = None
    for folder, origin in folders:
        try:
            ds = Dataset(folder, origin)
        except (DatasetError, OSError, ValueError) as e:
            if (Path(folder) / "latest.json").exists():
                log.warning("统计数据 %s 不能用：%s", folder, e)
            continue
        if best is None or (ds.snapshot_at, ds.patch) > (best.snapshot_at, best.patch):
            best = ds
    return best


PACK_FORMAT = "aramhex-pack-1"


def _dump_json(obj) -> bytes:
    """和数据仓库导出时完全一样的写法，还原出的文件和原文件逐字节一致。"""
    return (json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


def make_pack(folder: Path, manifest: dict) -> bytes:
    """一份数据 → 压缩包（官网镜像生成，和 unpack 配对）。"""
    import lzma
    files = {}
    for name in manifest["files"]:
        obj = json.loads((Path(folder) / name).read_text(encoding="utf-8"))
        if name.endswith("hero_augments.json"):
            obj = {h: [[int(a), s["games"], s["wins"], 1 if s.get("low_sample") else 0] for a, s in sorted(d.items())]
                   for h, d in obj.items()}
        files[name] = obj
    raw = json.dumps({"format": PACK_FORMAT, "files": files}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return lzma.compress(raw.encode("utf-8"), preset=9 | lzma.PRESET_EXTREME)


def unpack(blob: bytes) -> dict[str, bytes]:
    """官网镜像上的压缩包（xz）→ {文件名: 原文件字节}。hero_augments 在包里是 [海克斯ID, 局数, 胜场, 样本少]，
    胜率按导出时的算法 round(胜场/局数, 6) 还原。"""
    import lzma
    pack = json.loads(lzma.decompress(blob))
    if pack.get("format") != PACK_FORMAT:
        raise DatasetError("压缩包格式不支持")
    out = {}
    for name, obj in pack["files"].items():
        if name.endswith("hero_augments.json"):
            obj = {h: {str(a): {"games": g, "low_sample": bool(low), "win_rate": round(w / g, 6) if g else 0.0, "wins": w}
                       for a, g, w, low in rows} for h, rows in obj.items()}
        out[name] = _dump_json(obj)
    return out


def download(base_url: str, manifest: dict, dest: Path, timeout: float = 30, progress=None) -> None:
    """按清单下载一份数据到 dest（先写到旁边的临时目录，全部校验通过才换上）。progress(已下载字节, 总字节)。
    先试官网镜像上的压缩包（约小一半），还原后按清单逐个核对 SHA-256；没有或不对就逐个文件下载。"""
    check_manifest(manifest)
    path = str(manifest.get("path") or "").strip("/")
    if not path or ".." in path:
        raise DatasetError("数据清单没有 path")
    tmp = dest.with_name(dest.name + ".new")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    total = sum(int(f.get("size") or 0) for f in manifest["files"].values())
    done = [0]

    def on_bytes(n):
        done[0] += n
        if progress:
            progress(done[0], total)
    files = None
    try:
        files = unpack(http.fetch(f"{base_url}/{path}.pack.xz", timeout=timeout, headers=cloud.headers(base_url + "/"),
                                  on_bytes=lambda n: progress and progress(0, 0)))
        log.info("统计数据用压缩包下载：%s", base_url)
    except Exception as e:
        log.debug("没有可用的压缩包（%s），逐个文件下载", e)
    try:
        for name, f in manifest["files"].items():
            body = files.get(name) if files else None
            if body is None or hashlib.sha256(body).hexdigest() != f["sha256"]:
                if files:
                    log.info("压缩包里的 %s 对不上，改为逐个文件下载", name)
                    files = None
                body = http.fetch(f"{base_url}/{path}/{name}", timeout=timeout, headers=cloud.headers(base_url + "/"),
                                  on_bytes=on_bytes)
            if hashlib.sha256(body).hexdigest() != f["sha256"] or ("size" in f and len(body) != f["size"]):
                raise DatasetError(f"{name} 校验不对（下载不完整或文件被改过）")
            out = tmp / name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(body)
        (tmp / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        Dataset(tmp, "downloaded")  # 整份再核一遍（含可消费结构）
        old = dest.with_name(dest.name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        if dest.exists():
            dest.rename(old)
        tmp.rename(dest)
        shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


class PublicStore(DataStore):
    def __init__(self, root: Path, refresh_hours: float = 6.0, bundled: Path | None = None,
                 check_updates: bool = True, base_url: str | list[str] | None = None,
                 tag_hosts=None, tag_fmt: str = DATA_TAG_FMT, event_source=None, managed_official: bool = False):
        super().__init__(root, refresh_hours, managed=managed_official)
        self.bundled = Path(bundled or BUNDLED_DIR)
        self.downloaded = self.root / "public"
        self.event_dir = self.root / "public-event"  # 活动规则的数据（首次必棱彩等）
        self.event: dict | None = None               # 正在进行的活动 {mode, label}（云控）
        self.event_fallback = False                  # 有活动但还没有活动数据：先用常规
        if event_source is None:
            from .. import cloud
            event_source = cloud.event if base_url is None else (lambda: None)
        self._event_source = event_source
        self.check_updates = check_updates
        if base_url is None:
            from .. import cloud
            site = [f"{s}/u/data" for s in cloud.sites()]  # 云控换了官网地址时新的在前，内置在后
            urls = list(dict.fromkeys([u.rstrip("/") for u in cloud.mirrors("data")] + site + DATA_SOURCES))
        else:
            urls = [base_url] if isinstance(base_url, str) else base_url
        self.base_urls = [u.rstrip("/") for u in urls]
        # 默认地址时才按 tag 试探（测试里给了地址就不去连真的 jsDelivr）
        self.tag_hosts = tuple(DATA_TAG_HOSTS if tag_hosts is None and base_url is None else (tag_hosts or ()))
        self.tag_fmt = tag_fmt
        self.data_source: str | None = None  # 上次成功取到清单的地址
        self.band: str | None = None
        self.dataset: Dataset | None = None
        self.data_updated_at: float | None = None
        self.sync_error: str | None = None
        self.sync_progress: dict | None = None  # 下载统计数据时 {done, total}（字节）
        self.sync_every_s = SYNC_EVERY_S  # 多久查一次数据仓库；引擎按云控 update.data_minutes 改
        self.hold = lambda: False  # 返回 True 时（正在对局）不在后台换数据，打完再换，一局里推荐前后一致
        self.activation_guard = lambda: self._lock
        self.data_note: dict | None = None   # 后台换了新数据：{id, text}，界面在游戏外提示一次
        self.data_offer: dict | None = None  # 关了自动更新、发现有新数据：{id, text}，界面在游戏外问一次
        self._last_sync = 0.0
        self._sync_lock = threading.Lock()
        self._views: dict = {}
        self.reload_dataset()

    # ---------- 数据集 ----------

    def reload_dataset(self) -> bool:
        """重新挑选数据；换了一份返回 True。有活动且有活动数据时用活动的，否则用常规的。"""
        if self.hold():
            return False
        ds = load_best([(self.downloaded, "downloaded"), (self.bundled, "bundled")])
        self.event_fallback = False
        if self.event:
            ev = load_best([(self.event_dir, "downloaded")])
            if ev is not None and ev.mode == self.event["mode"]:
                ds = ev
            else:
                self.event_fallback = True
        with self.activation_guard():
            if self.hold():
                return False
            changed = ds is not None and (self.dataset is None or ds.revision != self.dataset.revision)
            if changed or ds is None:
                self.dataset = ds
                self._views.clear()
            self._apply_ranks()
        return changed

    def _activate_pending_data(self) -> bool:
        """下载目录不覆盖当前资料；安全时才换目录并刷新内存。"""
        with self.activation_guard():
            if self.hold():
                return False
            promoted = False
            for dest in (self.downloaded, self.event_dir):
                pending = dest.with_name(dest.name + "-pending")
                if not (pending / "latest.json").is_file():
                    continue
                try:
                    Dataset(pending, "downloaded")  # 不完整的暂存目录不能进入使用状态。
                except (OSError, ValueError, KeyError, TypeError) as e:
                    # 坏的暂存目录删掉，下次重新下载；留着的话每次都卡在这里，再也换不了数据
                    log.warning("下载的统计数据不完整，删掉重下：%s", e)
                    shutil.rmtree(pending, ignore_errors=True)
                    continue
                backup = dest.with_name(dest.name + f".previous-{time.time_ns()}") if dest.exists() else None
                if backup is not None:
                    dest.rename(backup)
                try:
                    pending.rename(dest)
                except OSError:
                    if backup is not None:
                        backup.rename(dest)
                    raise
                if backup is not None:
                    shutil.rmtree(backup, ignore_errors=True)  # 换成功了，旧的一份不用留（每次更新约 3 MB）
                promoted = True
            if not promoted or not self.reload_dataset():
                return False
            self.data_updated_at = time.time()
            self.data_offer = None
            self.data_note = {"id": self.dataset.revision,
                              "text": "数据已更新：" + self._describe(self.dataset.manifest)}
            return True

    def sync_data(self, force: bool = False, manual: bool = False) -> bool:
        """查数据仓库有没有更新的统计数据，有就下载并换上。下载了新数据返回 True。
        manual=True：用户点了「检查更新」或同意了更新提示，绕过自动更新开关；选人/对局中仍推迟。
        后台查时：正在对局就先不查（打完再查）；关了自动更新只看有没有新的，有就记到 data_offer 等用户点头。"""
        if self.hold():
            log.debug("对局中，统计数据更新推迟到打完")
            return False
        if not self._sync_lock.acquire(blocking=False):
            return False
        try:
            changed_pending = self._activate_pending_data()
            if not force and time.time() - self._last_sync < self.sync_every_s:
                return changed_pending
            self._last_sync = time.time()
            if not self.check_updates and not manual:
                self._look_for_offer()
                return changed_pending
            event = self._event_source()
            if event != self.event:
                self.event = event
                self.reload_dataset()
            errors, tried_tags = [], False
            for base in self.base_urls:  # 一个地址连不上、或下载的文件校验不过，就换下一个
                if not tried_tags and self.tag_hosts and base != self.base_urls[0]:
                    tried_tags = True  # GitHub 连不上了：先按 tag 试探 jsDelivr 上更新的数据
                    tag_base = self._probe_tag()
                    if tag_base:
                        try:
                            changed = self._sync_from(tag_base)
                            self.data_source, self.sync_error = tag_base, None
                            return changed or changed_pending
                        except Exception as e:
                            errors.append(f"{tag_base.split('/')[2]}（tag）：{e}")
                try:
                    changed = self._sync_from(base)
                except Exception as e:
                    errors.append(f"{base.split('/')[2]}：{e}")
                    log.info("从 %s 检查统计数据更新失败：%s", base, e)
                    continue
                self.data_source, self.sync_error = base, None
                if self.event:
                    try:
                        changed = self._sync_event(base) or changed
                    except Exception as e:
                        log.info("活动数据下载失败：%s", e)
                # 开头已经换上了暂存的新数据、远端又没有更新的：也算换了（手动检查不能说「已是最新」）
                return changed or changed_pending
            # 都连不上很常见：用手头的数据，不打扰用户
            self.sync_error = "；".join(errors)[:300]
            return changed_pending
        finally:
            self._sync_lock.release()

    def _probe_tag(self, steps: int = 30) -> str | None:
        """从当前数据的序号往后试 data-v<序号+1>、+2……，返回最新一份的地址；没有更新的返回 None。"""
        seq = self.dataset.seq if self.dataset else 0
        for host in self.tag_hosts:
            best, k, answered = None, seq + 1, False
            for _ in range(steps):
                base = self.tag_fmt.format(host=host, seq=k)
                try:
                    r = requests.get(f"{base}/latest.json", headers=cloud.headers(base + "/"), timeout=15)
                except requests.RequestException:
                    break
                answered = True
                try:
                    ok = r.status_code == 200 and int(r.json().get("seq") or 0) == k
                except ValueError:
                    ok = False
                if not ok:
                    break
                best, k = base, k + 1
            if answered:
                return best  # 这个节点答复了（有或没有更新），不用再问别的节点
        return None

    def _newer(self, remote: dict) -> bool:
        cur = self.dataset
        return not cur or (str(remote.get("snapshot_at") or ""), str(remote.get("patch") or "")) > (cur.snapshot_at, cur.patch)

    @staticmethod
    def _describe(remote: dict) -> str:
        """「16.20 版本，截至 10-17，3699 万局」"""
        parts = [f"{remote['patch']} 版本"] if remote.get("patch") else []
        if remote.get("snapshot_at"):
            parts.append("截至 " + str(remote["snapshot_at"])[5:10])
        m = remote.get("matches")
        m = m.get("valid") if isinstance(m, dict) else m
        if isinstance(m, int) and m > 0:
            parts.append(f"{m / 1e4:.0f} 万局" if m >= 1e4 else f"{m} 局")
        return "，".join(parts)

    def _look_for_offer(self) -> None:
        """关了自动更新时：只读清单，看看有没有更新的数据（不下载）。连不上就算了，下次再看。"""
        for base in self.base_urls:
            try:
                r = requests.get(f"{base}/latest.json", headers=cloud.headers(base + "/"), timeout=15)
                r.raise_for_status()
                remote = check_manifest(r.json())
            except Exception as e:
                log.info("从 %s 查看统计数据失败：%s", base, e)
                continue
            if self._newer(remote):
                log.info("发现新统计数据（%s），自动更新关着，等用户确认", self._describe(remote))
                self.data_offer = {"id": f"{remote.get('patch')}|{remote.get('snapshot_at')}",
                                   "text": self._describe(remote)}
            return

    def _sync_from(self, base: str) -> bool:
        r = requests.get(f"{base}/latest.json", headers=cloud.headers(base + "/"), timeout=15)
        r.raise_for_status()
        remote = check_manifest(r.json())
        if not self._newer(remote):
            return False
        log.info("统计数据有更新：%s（样本截至 %s），从 %s 下载", remote.get("patch"), remote.get("snapshot_at"), base)
        self.sync_progress = {"done": 0, "total": 0}  # 界面据此显示"统计数据更新中 x / y MB"
        try:
            download(base, remote, self.downloaded.with_name(self.downloaded.name + "-pending"),
                     progress=lambda d, t: setattr(self, "sync_progress", {"done": d, "total": t}))
        finally:
            self.sync_progress = None
        return self._activate_pending_data()

    def _sync_event(self, base: str) -> bool:
        """活动期间：从数据仓库的 index.json 找同一规则最新的一份，下载到 public-event/。"""
        r = requests.get(f"{base}/index.json", headers=cloud.headers(base + "/"), timeout=15)
        r.raise_for_status()
        idx = r.json()
        cands = [dict(d, schema_version=d.get("schema_version", idx.get("schema_version")))
                 for d in idx.get("datasets") or [] if d.get("mode") == self.event["mode"]]
        if not cands:
            return False
        remote = max(cands, key=lambda d: (str(d.get("snapshot_at") or ""), str(d.get("patch") or "")))
        cur = load_best([(self.event_dir, "downloaded")])
        if cur and cur.mode == self.event["mode"] and \
                (str(remote.get("snapshot_at") or ""), str(remote.get("patch") or "")) <= (cur.snapshot_at, cur.patch):
            return False
        log.info("下载活动数据：%s %s", self.event["label"], remote.get("patch"))
        download(base, remote, self.event_dir.with_name(self.event_dir.name + "-pending"))
        return self._activate_pending_data()

    def refresh(self, force: bool = False) -> None:
        super().refresh(force)
        self.sync_data(force)

    # ---------- 分段 ----------

    def set_band(self, band: str | None) -> None:
        with self._lock:
            band = norm_band(band)
            self.band = band if band in BANDS else None
            self._apply_ranks()

    def bands_available(self) -> bool:
        return bool(self.dataset and self.dataset.bands_available())

    def effective_band(self) -> str | None:
        """实际使用的分数段：数据里没有分段时退回全体。"""
        return self.band if self.band and self.bands_available() else None

    def _apply_ranks(self) -> None:
        """英雄排名、胜率和全英雄海克斯胜率（未识别英雄时的兜底）换成当前分段的。调用方持锁。"""
        if not self.dataset:
            self.ranks = {}
            return
        heroes, hero_augs = self.dataset.table(self.effective_band())
        total = sum(h["games"] for h in heroes.values()) or 1
        order = sorted(heroes, key=lambda h: (-heroes[h]["win_rate"], int(h)))
        agg: dict[str, list[int]] = {}
        for augs in hero_augs.values():
            for aid, a in augs.items():
                s = agg.setdefault(aid, [0, 0])
                s[0] += a["games"]
                s[1] += a["wins"]
        self.ranks = {"source": "public", "patch": self.dataset.patch, "band": self.effective_band(),
                      "heroes": {hid: {"win": heroes[hid]["win_rate"], "games": heroes[hid]["games"],
                                       "pick": heroes[hid]["games"] / total, "rank": i + 1}
                                 for i, hid in enumerate(order)},
                      "augments": {aid: {"win": w / n, "games": n} for aid, (n, w) in agg.items() if n}}

    def excluded_augments(self) -> dict[str, str]:
        """数据集 meta.json 的 excluded_augments：{"1337": {"name": ..., "reason": ...}}。"""
        ex = (self.dataset.meta if self.dataset else {}).get("excluded_augments") or {}
        return {str(aid): str((v or {}).get("reason") or "") if isinstance(v, dict) else str(v or "")
                for aid, v in ex.items()} if isinstance(ex, dict) else {}

    def provisional_augments(self) -> dict[str, dict]:
        """meta.json 的 provisional_augments（scripts/pbe_overlay.py 生成的过渡数据才有）。"""
        pv = (self.dataset.meta if self.dataset else {}).get("provisional_augments") or {}
        return {str(aid): v for aid, v in pv.items() if isinstance(v, dict) and v.get("games")} if isinstance(pv, dict) else {}

    def label(self) -> str:
        if not self.dataset:
            return "暂无统计数据"
        band = self.effective_band()
        return f"自采统计·{self.dataset.patch}版本" + (f"·{BANDS[band]}" if band else "")

    # ---------- 查询（panel / recommend 用） ----------

    def band_shrink(self) -> int | None:
        """当前分段和全体综合用的 K（见 _band_shrink）；没选分段为 None。"""
        band = self.effective_band()
        return self.dataset.band_shrink(band) if band and self.dataset else None

    def win_data_all(self, hero_id: str) -> dict | None:
        """单英雄 × 海克斯胜率（全体，不管选了哪个分段）。"""
        return self.win_data(hero_id, band=None)

    def win_data(self, hero_id: str, allow_network: bool = True, band: str | None = "") -> dict | None:
        """单英雄 × 海克斯胜率（当前分段；band=None 为全体）。"""
        if not self.dataset:
            return None
        band = self.effective_band() if band == "" else band
        heroes, hero_augs = self.dataset.table(band)
        h = heroes.get(str(hero_id))
        if not h:
            return None
        return {"source": "public", "label": self.label(), "patch": self.dataset.patch,
                "sample_games": h["games"], "wins": h["wins"], "sample_win": h["win_rate"],
                "augments": {aid: {"games": a["games"], "wins": a["wins"], "win": a["win_rate"],
                                   "low_sample": a.get("low_sample", False)}
                             for aid, a in (hero_augs.get(str(hero_id)) or {}).items()},
                "band": band, "band_label": BANDS.get(band)}

    def _hero_rows(self, name: str, hero_id: str) -> tuple[list[dict], str]:
        """hero_spells / hero_items 里这个英雄的各行：[{key, games, wins, win}]，按局数从多到少。"""
        if not self.dataset:
            return [], ""
        band = self.effective_band()
        table = self.dataset.optional_table(name, band)
        rows = []
        for key, x in (table.get(str(hero_id)) or {}).items():
            if not isinstance(x, dict):
                continue
            g, w = x.get("games"), x.get("wins")
            if isinstance(g, int) and g > 0 and isinstance(w, int) and 0 <= w <= g:
                rows.append({"key": str(key), "games": g, "wins": w, "win": w / g})
        rows.sort(key=lambda r: -r["games"])
        return rows, self.label()

    def hero_spells(self, hero_id: str) -> tuple[list[dict], str]:
        """自采统计的召唤师技能组合（hero_spells.json：英雄 → "4+32" → 局数/胜场）。没有这个文件返回 []。
        返回 ([{spells: [ID, ID], games, win, pick}], 来源说明)。"""
        rows, label = self._hero_rows("hero_spells.json", hero_id)
        total = sum(r["games"] for r in rows) or 1
        out = [{"spells": [x for x in r["key"].split("+") if x], "games": r["games"], "win": r["win"],
                "pick": r["games"] / total} for r in rows]
        return [r for r in out if len(r["spells"]) == 2], label

    def hero_items(self, hero_id: str) -> tuple[list[dict], str]:
        """自采统计的装备（hero_items.json：英雄 → 装备 ID → 局数/胜场）。没有这个文件返回 []。"""
        rows, label = self._hero_rows("hero_items.json", hero_id)
        return [{"item": r["key"], "games": r["games"], "win": r["win"]} for r in rows], label

    def augment_items(self, hero_id: str, augment_id: str) -> dict | None:
        """按英雄、海克斯配置的装备建议；独立于普通出装统计，保持数据提供的顺序。"""
        if not self.dataset:
            return None
        table = self.dataset.optional_table("hero_augment_items.json")
        hero = table.get(str(hero_id))
        cfg = hero.get(str(augment_id)) if isinstance(hero, dict) else None
        if not isinstance(cfg, dict) or cfg.get("source") not in ("stats", "community", "manual"):
            return None
        raw = cfg.get("items")
        if not isinstance(raw, list):
            return None
        rows, seen = [], set()
        for x in raw:
            if not isinstance(x, dict):
                continue
            iid = str(x.get("item") or "")
            if not iid.isascii() or not iid.isdigit() or len(iid) > 6 or iid in seen:
                continue
            meta = self.items.get(iid) or {}
            name = meta.get("name") or x.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            g, w = x.get("games"), x.get("wins")
            if (g is not None or w is not None) and not (type(g) is int and g > 0 and type(w) is int and 0 <= w <= g):
                continue
            show = cfg.get("show_winrate", True) is True and x.get("show_winrate", True) is True
            rows.append({"item": iid, "name": name[:24], "icon": meta.get("icon"),
                         "games": g, "win": w / g if show and g else None})
            seen.add(iid)
            if len(rows) == 3:  # 卡片另一侧的空位只放三行；人工推荐的先后顺序不重排
                break
        return {"source": cfg["source"], "items": rows} if rows else None

    def hero(self, hero_id: str, allow_network: bool = True) -> dict | None:
        """官方出装、加点 + 海克斯列表（有统计的在前，带出场占比；官方层级一并保留）。"""
        data = self.official_hero(hero_id, allow_network) or {}
        stats = self.win_data(hero_id)
        if not stats:
            return data or None
        official = {a["id"]: a for a in data.get("augments") or []}
        augments = []
        for aid, a in stats["augments"].items():
            t = official.pop(aid, {})
            augments.append({**t, "id": aid, "rarity": t.get("rarity") or self.augments.get(aid, {}).get("rarity"),
                             "pick": a["games"] / stats["sample_games"]})
        augments += list(official.values())  # 统计里还没有的（新海克斯）：只有官方层级
        return {**data, "augments": augments}

    def status(self) -> dict:
        s = super().status()
        ds = self.dataset
        meta = ds.meta if ds else {}
        s.update(dataset=bool(ds), dataset_patch=ds.patch if ds else None,
                 dataset_snapshot=ds.snapshot_at if ds else None, dataset_origin=ds.origin if ds else None,
                 dataset_revision=ds.revision if ds else None, dataset_window=meta.get("window"),
                 dataset_matches=(meta.get("matches") or {}).get("valid") if isinstance(meta.get("matches"), dict) else meta.get("matches"),
                 dataset_collected=(meta.get("matches") or {}).get("collected") if isinstance(meta.get("matches"), dict) else None,
                 dataset_regions=meta.get("regions"),
                 dataset_label=self.label(), bands_available=self.bands_available(),
                 band=self.band, band_effective=self.effective_band(),
                 dataset_mode=ds.mode if ds else None,
                 dataset_mode_label=(ds.mode_label or ds.mode) if ds and ds.mode != "normal" else None,
                 event_label=(self.event or {}).get("label"), event_fallback=self.event_fallback,
                 data_updated_at=self.data_updated_at, sync_error=self.sync_error,
                 data_progress=self.sync_progress, data_note=self.data_note, data_offer=self.data_offer)
        return s
