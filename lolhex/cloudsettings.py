"""云控设置（1.1.1）：不发新版本也能改玩家的设置。

云控 config.json 里写 "settings"：一条或多条规则（列表），每条：
    {"id": "speed-saver-1022",          # 规则编号：force 的提示每个编号只弹一次
     "versions": ["1.1.1"],             # 只对这些版本生效；不写 = 所有支持云控设置的版本
     "percent": 20,                     # 只对一部分电脑（按匿名编号分组 0–99）；不写 = 100
     "defaults": {"speed": "saver"},    # 改默认值：只改玩家自己没动过的项，写进设置文件
     "force": {"overlay": false},       # 应急强制：玩家改过也盖掉，但不写进设置文件，云控撤掉就恢复
     "reason": "……"}                    # force 时给玩家看的一句原因

能云控的项和取值范围见 SPEC；不在表里的项、取值不对的都忽略（记日志）。
云控配置存在本地（cloud.json），断网时照样按上次的生效。
"""

from __future__ import annotations

import logging

from . import cloud
from .config import SPEEDS, Settings
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
    "refresh_hours": _num(1, 72),
    "scan_interval_ms": _num(100, 5000, integer=True),
    "idle_scan_ms": _num(100, 5000, integer=True),
    "bootstrap_interval_s": _num(1, 120),
    "match_threshold": _num(50, 99),
    "smoothing_games": _num(0, 10000, integer=True),
    "overlay_corner": _one_of({"top-right", "top-left"}),
    "badge_position": _one_of({"top", "bottom"}),
    "data_band": _one_of({"", "casual", "mid", "high"}),
    "speed": _one_of(set(SPEEDS)),
    "game_lang": _one_of(LANG_CODES),
}


def rules(cfg: dict) -> list[dict]:
    raw = cfg.get("settings")
    if isinstance(raw, dict):
        raw = [raw]
    return [r for r in raw or [] if isinstance(r, dict)]


def _applies(rule: dict, version: str, install_id: str | None) -> bool:
    vs = rule.get("versions")
    if isinstance(vs, list) and vs and version not in [str(v) for v in vs]:
        return False
    pct = rule.get("percent", 100)
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        pct = 100
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


def apply(settings: Settings, cfg: dict, version: str) -> dict:
    """按云控改设置。返回 {"changed": [改了值的项], "reasons": [(规则编号, 原因)]（force 带原因的）}。
    defaults 改动会写进设置文件（调用方存）；force 的项记在 settings.forced（存文件时写回原值）。"""
    install_id = settings.install_id if settings.send_usage else None
    defaults, force, reasons = {}, {}, []
    for r in rules(cfg):
        if not _applies(r, version, install_id):
            continue
        rid = str(r.get("id") or "")
        defaults.update(_valid(r.get("defaults"), f"规则 {rid} defaults"))
        f = _valid(r.get("force"), f"规则 {rid} force")
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
    if changed:
        log.info("云控设置生效：%s", "，".join(f"{k}={getattr(settings, k)!r}" for k in changed))
    return {"changed": changed, "reasons": reasons}
