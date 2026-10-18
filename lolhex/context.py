"""看对面阵容的海克斯：对面有某些英雄时，这个海克斯比平时强多少。

数据（data/context.json）：同一英雄拿和没拿这个海克斯的胜率差（百分点），按对面有没有某个英雄分开算；
只收比「对面一个都没有」时高出 0.5 个点以上的英雄。例：掷骰狂人，对面有玛尔扎哈 +5.1、婕拉 +4.5，没有时 +2.0。
对面阵容从游戏内 2999 接口读（设置里打开），读不到就不显示。
测试版先内置在软件里；以后随统计数据一起下发。
"""

from __future__ import annotations

import json
from importlib import resources

MIN_EXTRA = 0.5  # 比「对面一个都没有」高出这么多才提


def load() -> dict:
    try:
        return json.loads(resources.files("lolhex.data").joinpath("context.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


_TABLE = load()


def enemy_note(aid: str | None, enemies: list[str] | None, heroes: dict, table: dict | None = None,
               limit: int = 2) -> str | None:
    """「对面玛尔扎哈 +5.1、婕拉 +4.5」（最多 limit 个，角标窄）；这个海克斯不看阵容、或对面没有相关英雄时返回 None。"""
    t = ((table if table is not None else _TABLE).get("augments") or {}).get(str(aid or ""))
    if not t or not enemies:
        return None
    base = t.get("base") or 0
    hits = sorted(((v, h) for h, v in (t.get("enemies") or {}).items()
                   if h in {str(e) for e in enemies} and v - base >= MIN_EXTRA), reverse=True)
    if not hits:
        return None
    return "对面" + "、".join(f"{(heroes.get(h) or {}).get('name') or h} {v:+.1f}" for v, h in hits[:limit])
