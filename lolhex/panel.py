"""单英雄资料面板：胜率与评级、海克斯全表、出装、加点。主窗口和游戏内浮窗共用。"""

from __future__ import annotations

from . import recommend

MIN_SHARE = 0.1     # 出装按胜率排之前，先去掉出场不到同类最热门 1/10 的（样本太少，胜率说明不了什么）


# 召唤师技能组合：出场率 ≥3% 且至少 1000 局的，按胜率排，最多三组（用户 10-23 定，和官网数据页一致）。
# 以前是 ≥10%、最多两组：一半英雄只剩一组，格温的疾跑+雪球（5%、胜率最高、1 万多局）被挡掉。
# 一组都不够时（极少见）放出场最多的那一组。
SUMMONER_MIN_PICK = 0.03
SUMMONER_MIN_GAMES = 1000
SUMMONER_SHOW = 3


def sort_summoners(rows: list[dict]) -> list[dict]:
    enough = sorted((r for r in rows if r["pick"] >= SUMMONER_MIN_PICK and r["games"] >= SUMMONER_MIN_GAMES),
                    key=lambda r: -r["win"])
    return enough[:SUMMONER_SHOW] or sorted(rows, key=lambda r: -r["games"])[:1]


def by_win(rows: list[dict], min_games: int = 0) -> list[dict]:
    """有一定出场的里面按胜率从高到低排。出场率缺失时用场次比较。"""
    def share(x):
        return x.get("pick") or x.get("games") or 0
    top = max((share(x) for x in rows), default=0)
    keep = [x for x in rows if x.get("win") is not None and share(x) >= top * MIN_SHARE
            and (x.get("games") or min_games) >= min_games]
    return sorted(keep, key=lambda x: -x["win"])


def hero_panel(store, hero_key, k: int = 300, *, allow_network: bool = True) -> dict | None:
    hid = store.resolve_hero(hero_key)
    if not hid:
        return None
    data = store.hero(hid, allow_network=allow_network) or {}
    ag = store.win_data(hid, allow_network=allow_network) or {}
    ag_augs = ag.get("augments") or {}
    # 测试服数据的新海克斯：只显示测试服胜率，不算 Δ，不进「推荐海克斯」排行（不是同一个版本的数据）
    provisional = store.provisional_augments() if hasattr(store, "provisional_augments") else {}
    blend = recommend.band_blend(store, hid, ag, k)  # 选了分段：Δ 和三选一一样按分段、全体综合

    augs = []
    for a in data.get("augments", []):
        meta = store.augments.get(a["id"], {})
        x = ag_augs.get(a["id"]) or {}
        d = None
        if a["id"] in provisional:
            pass
        elif blend is not None:
            d = (blend.get(a["id"]) or {}).get("delta")
        elif x.get("win") is not None and x.get("games") and ag.get("sample_win") is not None:
            d = recommend.smoothed_delta(x["win"], x["games"], ag["sample_win"], k)
        augs.append({**a, "name": meta.get("name", a["id"]), "icon": meta.get("icon"),
                     "desc": meta.get("desc"), "win": x.get("win"), "games": x.get("games"),
                     "source": ("pbe" if a["id"] in provisional else "public") if x else None,
                     "delta": None if d is None else round(d, 2),
                     "sample": recommend.sample_level(((blend or {}).get(a["id"]) or {}).get("games_all")
                                                      or x.get("games"))})

    items = store.items

    def item(i, name=None):
        it = items.get(str(i), {})
        return {"id": str(i), "name": it.get("name") or name or str(i), "icon": it.get("icon")}

    b = data.get("builds") or {}
    # 官方出装都按胜率排（只比有一定出场的）
    builds = {
        "full": [{**x, "items": [item(i) for i in x["items"]]} for x in by_win(b.get("full", []))],
        "core": [{**x, "items": [item(i) for i in x["items"]]} for x in by_win(b.get("core", []))],
        "first": [{**x, "item": item(x["item"])} for x in by_win(b.get("first", []))],
        "shoes": [{**x, "item": item(x["item"])} for x in by_win(b.get("shoes", []))],
        "start": [{**x, "items": [item(i) for i in x["items"]]} for x in by_win(b.get("start", []))],
    }
    # 自采统计的召唤师技能、装备（数据里有 hero_spells.json / hero_items.json 才有）
    summoners, sum_src, item_stats = [], "", []
    if hasattr(store, "hero_spells"):
        spells = store.spells
        rows, sum_src = store.hero_spells(hid)
        summoners = [{**r, "spells": [{"id": x, "name": (spells.get(x) or {}).get("name") or x,
                                       "icon": (spells.get(x) or {}).get("icon")} for x in r["spells"]]}
                     for r in sort_summoners(rows)]
        rows, _ = store.hero_items(hid)
        item_stats = [{**r, "item": item(r["item"])} for r in rows[:6]]
    rank = (store.ranks.get("heroes") or {}).get(hid, {})
    n_heroes = len(store.ranks.get("heroes") or {}) or None
    return {
        "hero": store.heroes.get(hid),
        "rank": {**rank, "total": n_heroes},
        "augments": augs, "builds": builds, "skills": data.get("skills", []), "spells": data.get("spells", {}),
        "partners": [{**p, "hero": store.heroes.get(p["hero"], {"id": p["hero"]})}
                     for p in data.get("partners", [])],
        "date": data.get("date"), "patch": store.static.get("patch"),
        "data_patch": ag.get("patch"), "data_revision": (store.status().get("dataset_revision")),
        "splash": data.get("splash"),
        "win_source": ag.get("label") or "暂无统计数据", "summoners": summoners,
        "summoner_source": sum_src if summoners else "", "summoner_source_date": str(getattr(getattr(store, "dataset", None), "snapshot_at", "") or "")[:10] or None,
        "item_stats": item_stats,
    }
