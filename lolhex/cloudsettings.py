"""云控设置（1.1.1）：不发新版本也能改玩家的设置。

云控 config.json 里写 "settings"：一条或多条规则（列表），每条：
    {"id": "speed-saver-1022",          # 规则编号：force 的提示每个编号只弹一次
     "versions": ["1.1.1"],             # 只对这些版本生效；不写 = 所有支持云控设置的版本
     "percent": 20,                     # 只对一部分电脑（按匿名编号分组 0–99）；不写 = 100
     "defaults": {"speed": "saver"},    # 改默认值：只改玩家自己没动过的项，写进设置文件
     "force": {"overlay": false},       # 应急强制：玩家改过也盖掉，但不写进设置文件，云控撤掉就恢复
     "reason": "……"}                    # force 时给玩家看的一句原因

1.1.9 起另有一段 "settings_v2"（格式同上，规则可以按电脑挑；1.1.8 及以前根本不读这一段，不会误伤）：
    "os": ["10"]                        # Windows 版本："10" / "11"
    "hybrid": true                      # 大小核（true）/ 只有一种核（false）；拿不到大小核信息的电脑不算
    "vendor": ["intel"]                 # "intel" / "amd"
    "cpu": ["13400F", "i7-137"]         # CPU 型号里含其中任一段（不分大小写）；读不到型号的电脑不算
    "ids": ["0123456789abcdef"]         # 指定匿名编号（关了使用统计的电脑没有编号，不算）
写了的条件都要满足，满足后再按 percent 分组（没有编号的电脑只进 percent 100 的规则）。
settings_v2 比 settings 严：每条都要写 versions（不写整条跳过，免得以后的版本也被选中）；
条件、versions、percent 有一项格式不对（如 "os": ["12"]、"percent": "50"），整条跳过；
有本版不认识的键（以后的新条件）也整条跳过，以 "_" 开头的键当注释。
settings 里写这些条件的规则整条跳过：1.1.8 及以前会当成没写、对所有电脑生效，所以条件只能写在 settings_v2。
点名、对照试验用 force：defaults 写进设置文件，规则撤了、电脑不再符合条件也不会改回去。
NO_FORCE 里的项只能改默认值，写在 force 里忽略（overlay_bg 详情的背景是个人喜好，1.2.0）。

能云控的项和取值范围见 SPEC；不在表里的项、取值不对的都忽略（记日志）。
云控配置存在本地（cloud.json），断网时照样按上次的生效。
"""

from __future__ import annotations

import logging
import re

from . import cloud
from .config import OVERLAY_BGS, SPEEDS, Settings
from .i18n import LANG_CODES

log = logging.getLogger(__name__)


def _bool(v):
    return v if isinstance(v, bool) else None


def _num(lo, hi, integer=False):
    def check(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            return None
        return int(v) if integer else float(v)
    return check


def _one_of(choices):
    def check(v):
        return v if isinstance(v, str) and v in choices else None
    return check


# 能云控的设置：项 → 检查函数（返回合法值，不合法返回 None）。
# 不在这里的（查分账号、匿名编号、使用统计开关、看过的公告等）云控改不了
SPEC = {
    "show_winrate": _bool,
    "check_data_updates": _bool,
    "scan_when_mode_unknown": _bool,
    "overlay": _bool,
    "overlay_on_demand": _bool,
    "refresh_hours": _num(1, 72),
    "scan_interval_ms": _num(100, 5000, integer=True),
    "idle_scan_ms": _num(100, 5000, integer=True),
    "idle_ocr_guard": _bool,
    "bootstrap_interval_s": _num(1, 120),
    "fallback_scan_s": _num(10, 900),
    "fallback_proven_s": _num(10, 3600),
    "fallback_first_s": _num(3, 900),
    "garbled_rescan_s": _num(1, 600),
    "garbled_rescan_max": lambda v: v if type(v) is int and 0 <= v <= 100 else None,
    "full_scan_gap_x": _num(0, 20),
    "auto_idle_factor": _num(0, 20),
    "vision_priority": _one_of({"normal", "below_normal", "lowest"}),
    "vision_eco": _bool,
    "vision_no_throttle": _bool,
    "recheck_seconds": _num(1, 60),
    "pending_seconds": _num(0.1, 5),
    "low_match_reads": lambda v: v if type(v) is int and 1 <= v <= 10 else None,
    "group_confirm": _bool,
    "active_hold_s": _num(0, 30),
    "calib_restore": _bool,
    "color_check": _one_of({"log", "reject", "off"}),
    "match_threshold": _num(50, 99),
    "smoothing_games": _num(0, 10000, integer=True),
    "overlay_corner": _one_of({"top-right", "top-left"}),
    "overlay_bg": _one_of({k for k, _ in OVERLAY_BGS}),
    "badge_position": _one_of({"top", "bottom"}),
    "data_band": _one_of({"", "casual", "mid", "high"}),
    "speed": _one_of(set(SPEEDS)),
    "game_lang": _one_of(LANG_CODES),
}

NO_FORCE = {"overlay_bg"}


def rules(cfg: dict) -> list[dict]:
    raw = cfg.get("settings")
    if isinstance(raw, dict):
        raw = [raw]
    return [r for r in raw or [] if isinstance(r, dict)]


LEGACY_KEYS = {"id", "versions", "percent", "defaults", "force", "reason"}
CONDITIONS = {"os", "hybrid", "vendor", "cpu", "ids"}
_skipped: set[tuple[str, str]] = set()  # 已记过日志的跳过规则（段落、编号），免得每次刷新云控都记


def v2_rules(cfg: dict) -> list[dict]:
    raw = cfg.get("settings_v2")
    if isinstance(raw, dict):
        raw = [raw]
    return [r for r in raw or [] if isinstance(r, dict)]


def _skip(where: str, rule: dict, why: str) -> bool:
    rid = str(rule.get("id") or "")
    if (where, rid) not in _skipped:
        _skipped.add((where, rid))
        log.info("云控设置：%s 的规则 %s %s，跳过", where, rid or "（没写编号）", why)
    return False


def _strs(v, ok) -> list[str] | None:
    """条件取值：字符串列表（单个字符串也行），每一项都要 ok；有一项不对返回 None，规则不生效。"""
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not v or not all(isinstance(x, str) and ok(x) for x in v):
        return None
    return v


_CHECK = {
    "os": lambda v: _strs(v, lambda x: x in ("10", "11")),
    "vendor": lambda v: _strs(v, lambda x: x.lower() in ("intel", "amd")),
    "cpu": lambda v: _strs(v, lambda x: bool(x.strip())),
    "ids": lambda v: _strs(v, lambda x: re.fullmatch(r"[0-9a-f]{16}", x) is not None),
    "hybrid": lambda v: [v] if isinstance(v, bool) else None,
}


def _bad_v2(rule: dict) -> str:
    """settings_v2 规则格式不对的地方；没问题返回空。"""
    unknown = sorted(str(k) for k in rule if k not in LEGACY_KEYS | CONDITIONS and not str(k).startswith("_"))
    if unknown:
        return "有本版不认识的 " + "、".join(unknown)
    bad = [k for k in sorted(CONDITIONS) if k in rule and _CHECK[k](rule[k]) is None]
    if bad:
        return "的条件 " + "、".join(bad) + " 格式不对"
    vs = rule.get("versions")
    if "versions" not in rule:
        return "没写 versions（settings_v2 每条都要写，免得以后的版本也被选中）"
    if not (isinstance(vs, list) and vs and all(isinstance(v, str) and v for v in vs)):
        return "的 versions 格式不对"
    pct = rule.get("percent", 100)
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100:  # NaN 比较也不成立
        return "的 percent 格式不对"
    return ""


def _machine_ok(rule: dict, machine, install_id: str | None) -> bool:
    """按电脑挑选的条件（已校验过格式）；没写的条件不限，本机拿不到这项信息的算不满足。
    machine 是取本机信息的函数，只在规则写了系统、大小核、厂商、型号时才调用。"""
    if "ids" in rule and (not install_id or install_id not in _CHECK["ids"](rule["ids"])):
        return False
    if not (CONDITIONS - {"ids"}) & set(rule):
        return True
    m = machine()
    if "os" in rule and (not m.get("os") or m["os"] not in _CHECK["os"](rule["os"])):
        return False
    if "hybrid" in rule and m.get("hybrid") is not rule["hybrid"]:
        return False
    if "vendor" in rule and (not m.get("vendor") or m["vendor"] not in [x.lower() for x in _CHECK["vendor"](rule["vendor"])]):
        return False
    if "cpu" in rule:
        cpu = (m.get("cpu") or "").lower()
        if not cpu or not any(x.strip().lower() in cpu for x in _CHECK["cpu"](rule["cpu"])):
            return False
    return True


def _applies(rule: dict, version: str, install_id: str | None, machine=None) -> bool:
    """machine 为 None：settings 里的老格式规则；否则是 settings_v2 的，machine 是取本机信息的函数。"""
    if machine is None:
        extra = sorted(str(k) for k in rule if k not in LEGACY_KEYS and not str(k).startswith("_"))
        if extra:
            why = "写了按电脑挑的条件（要写在 settings_v2）" if CONDITIONS & set(rule) else "有本版不认识的 " + "、".join(extra)
            return _skip("settings", rule, why)
    else:
        why = _bad_v2(rule)
        if why:
            return _skip("settings_v2", rule, why)
    vs = rule.get("versions")
    if isinstance(vs, list) and vs and version not in [str(v) for v in vs]:
        return False
    if machine is not None and not _machine_ok(rule, machine, install_id):
        return False
    pct = rule.get("percent", 100)
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        pct = 100  # 老格式写错按 100（1.1.1 起就这样）；settings_v2 前面已经挡掉
    if machine is not None and not install_id and pct < 100:
        return False  # 没有编号（关了使用统计）只进 100% 的规则；99.5 这种小数也不进
    rid = str(rule.get("id") or "")
    return cloud.bucket(f"{install_id}:{rid}" if install_id else None) < max(0, min(100, pct))


def _valid(items, where: str) -> dict:
    out = {}
    for k, v in (items or {}).items() if isinstance(items, dict) else ():
        check = SPEC.get(k)
        val = check(v) if check else None
        if val is None:
            log.info("云控设置：%s 里的 %s=%r 不认，忽略", where, k, v)
            continue
        out[k] = val
    return out


_last_hit: list[str] | None = None


def apply(settings: Settings, cfg: dict, version: str, machine: dict | None = None) -> dict:
    """按云控改设置。返回 {"changed": [改了值的项], "reasons": [(规则编号, 原因)]（force 带原因的）}。
    defaults 改动会写进设置文件（调用方存）；force 的项记在 settings.forced（存文件时写回原值）。
    machine：本机信息（见 diagnostics.machine），不传就在 settings_v2 规则用到时现读。"""
    global _last_hit
    got: list[dict] = [machine] if machine is not None else []

    def read_machine() -> dict:  # 用到时才读，读不到当什么都不知道（带这些条件的规则不生效）
        if not got:
            try:
                from .diagnostics import machine as read
                got.append(read())
            except Exception:
                log.exception("云控设置：读本机信息出错")
                got.append({})
        return got[0]

    install_id = settings.install_id if settings.send_usage else None
    defaults, force, reasons, hit = {}, {}, [], []
    for r, m in [(r, None) for r in rules(cfg)] + [(r, read_machine) for r in v2_rules(cfg)]:
        if not _applies(r, version, install_id, m):
            continue
        rid = str(r.get("id") or "")
        hit.append(rid)
        defaults.update(_valid(r.get("defaults"), f"规则 {rid} defaults"))
        f = _valid(r.get("force"), f"规则 {rid} force")
        for k in NO_FORCE & f.keys():
            log.info("云控设置：规则 %s 的 %s 只能写在 defaults，force 里忽略", rid, k)
            del f[k]
        force.update(f)
        if f and r.get("reason"):
            reasons.append((rid, str(r["reason"])[:200]))
    changed = []
    # 先撤掉上次强制、这次不再强制的项
    for k in [k for k in settings.forced if k not in force]:
        orig = settings.forced.pop(k)
        if getattr(settings, k) != orig:
            setattr(settings, k, orig)
            changed.append(k)
    for k, v in defaults.items():
        if k in settings.user_set or k in force:
            continue
        if getattr(settings, k) != v:
            setattr(settings, k, v)
            changed.append(k)
    for k, v in force.items():
        settings.forced.setdefault(k, getattr(settings, k))
        if getattr(settings, k) != v:
            setattr(settings, k, v)
            changed.append(k)
    if hit != _last_hit:  # 本机命中哪些规则：变了才记，看日志就知道分组试验分到了哪边
        _last_hit = hit
        m = got[0] if got else {}
        log.info("云控设置：本机命中规则 %s%s", "、".join(hit) or "无",
                 f"（系统={m.get('os') or '?'} 大小核={ {True: '是', False: '否'}.get(m.get('hybrid'), '?')} "
                 f"厂商={m.get('vendor') or '?'}）" if m else "")
    if changed:
        log.info("云控设置生效：%s", "，".join(f"{k}={getattr(settings, k)!r}" for k in changed))
    return {"changed": changed, "reasons": reasons}
