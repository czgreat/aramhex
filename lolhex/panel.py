"""单英雄资料面板：胜率与评级、海克斯全表、出装、加点。主窗口和游戏内浮窗共用。"""

from __future__ import annotations

from . import recommend

MIN_SHARE = 0.1     # 出装按胜率排之前，先去掉出场不到同类最热门 1/10 的（样本太少，胜率说明不了什么）


def by_win(rows: list[dict], min_games: int = 0) -> list[dict]:
    """有一定出场的里面按胜率从高到低排。出场率缺失时用场次比较。"""
    def share(x):
        return x.get("pick") or x.get("games") or 0
    top = max((share(x) for x in rows), default=0)
    keep = [x for x in rows if x.get("win") is not None and share(x) >= top * MIN_SHARE
            and (x.get("games") or min_games) >= min_games]
    return sorted(keep, key=lambda x: -x["win"])


def hero_panel(store, hero_key, k: int = 300) -> dict | None:
    hid = store.resolve_hero(hero_key)
    if not hid:
        return None
    data = store.hero(hid) or {}
    ag = store.win_data(hid) or {}
    ag_augs = ag.get("augments") or {}
    # 测试服数据的新海克斯：只显示测试服胜率，不算 Δ，不进「推荐海克斯」排行（不是同一个版本的数据）
    provisional = store.provisional_augments() if hasattr(store, "provisional_augments") else {}

    augs = []
    for a in data.get("augments", []):
        meta = store.augments.get(a["id"], {})
        x = ag_augs.get(a["id"]) or {}
        d = None
        if a["id"] in provisional:
            pass
        elif x.get("win") is not None and x.get("games") and ag.get("sample_win") is not None:
            d = recommend.smoothed_delta(x["win"], x["games"], ag["sample_win"], k)
        augs.append({**a, "name": meta.get("name", a["id"]), "icon": meta.get("icon"),
                     "desc": meta.get("desc"), "win": x.get("win"), "games": x.get("games"),
                     "source": ("pbe" if a["id"] in provisional else "public") if x else None,
                     "delta": None if d is None else round(d, 2),
                     "sample": recommend.sample_level(x.get("games"))})

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
        "win_source": ag.get("label") or "暂无统计数据", "summoners": [],
    }
