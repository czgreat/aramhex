"""三选一评分：以本英雄的海克斯胜率为主，官方层级只做兜底。

数据来源（依次）：
1. 自采统计：本英雄 × 这个海克斯的胜率（可按分段）；
2. 腾讯官方层级 S/A/B/C（没有胜率时粗估）；
3. 未识别英雄时：自采统计里这个海克斯在所有英雄上的合计胜率。

分数 = 相对该英雄基准胜率的Δ胜率（百分点）：
1. 贝叶斯平滑：smoothed = (胜率×场次 + 基准×k) / (场次 + k)，k 默认 300，小样本的高胜率会被拉回基准附近。
2. Δ = smoothed − 该英雄基准胜率。
3. 样本分档：<250 局仅参考、250–999 中等、≥1000 充足，显示在界面上。
4. 首选只在样本充足（≥1000 局）的海克斯里挑：冷门海克斯偶尔胜率高，多半是局数少碰巧打出来的。
   比同屏其他海克斯少一个数量级以上（<1/10）的，标"样本太少"并提示刷新，不管胜率多高；
   只是不足 1000 局的，不当首选、标"样本偏少"。三张都不足时才正常比较。
选取率只展示，不进分数：常拿不代表强。
统计里剔除的海克斯（数据集 excluded_augments，如尊我为王：完成任务后被替换，局末数据不准）标「无数据」，
不打分、不用官方层级兜底、不参与首选比较。
"""

from __future__ import annotations

from .data.tencent import RARITY_CN

TIER_PP = {"S": 1.5, "A": 0.5, "B": -0.5, "C": -1.5, "D": -2.5}
REROLL_BELOW = -0.5  # 低于英雄基准 0.5pp 以上：可考虑刷新
REROLL_GAP = 2.0     # 比首选低 2pp 以上：可考虑刷新
RELIABLE_GAMES = 1000  # 本英雄局数达到这个数才能当首选
TOO_FEW_RATIO = 0.1    # 局数不到同屏最多那张的 1/10：样本太少，提示刷新
NODATA_NOTE = "完成任务后会被替换，统计不准"


def sample_level(games: int | None) -> str:
    if not games:
        return "none"
    if games < 250:
        return "low"
    if games < 1000:
        return "medium"
    return "high"


SAMPLE_CN = {"none": "无样本", "low": "样本少，仅参考", "medium": "样本中等", "high": "样本充足"}


def smoothed_delta(win: float | None, games: int | None, base: float | None, k: int) -> float | None:
    """贝叶斯平滑后的Δ胜率（百分点）。"""
    if win is None or base is None or not games:
        return None
    s = (win * games + base * k) / (games + k)
    return (s - base) * 100


def win_ranks(augments: dict, static: dict) -> dict:
    """本英雄同稀有度里按原始胜率排名；胜率相同名次相同。"""
    groups = {}
    for aid, stats in augments.items():
        win = stats.get("win")
        rarity = (static.get(aid) or {}).get("rarity")
        if rarity and type(win) in (int, float) and 0 <= win <= 1 and stats.get("games", 0) > 0:
            groups.setdefault(rarity, []).append((aid, win))
    ranks = {}
    for rows in groups.values():
        rows.sort(key=lambda row: (-row[1], row[0]))
        previous = None
        rank = 0
        for position, (aid, win) in enumerate(rows, 1):
            if previous is None or win != previous:
                rank = position
            ranks[aid] = {"rank": rank, "rank_total": len(rows), "rank_basis": "hero_rarity_win_rate"}
            previous = win
    return ranks


def evaluate(store, hero_id: str | None, slots: list[str | None], k: int = 300) -> dict:
    hero = store.hero(hero_id) if hero_id else None
    ag = store.win_data(hero_id) if hero_id else None
    label = (ag or {}).get("label") or "自采统计"
    ag_augs = (ag or {}).get("augments") or {}
    ranks_by_win = win_ranks(ag_augs, store.augments)
    base = (ag or {}).get("sample_win")
    if base is None and hero_id:
        base = ((store.ranks.get("heroes") or {}).get(str(hero_id)) or {}).get("win")
    official = {a["id"]: a for a in (hero or {}).get("augments", [])}
    excluded = store.excluded_augments()
    rarity_total: dict[str, int] = {}
    for a in official.values():
        if a.get("tier") and a.get("rarity"):
            rarity_total[a["rarity"]] = rarity_total.get(a["rarity"], 0) + 1
    global_augs = store.ranks.get("augments") or {}
    total_games = sum(g.get("games") or 0 for g in global_augs.values())
    global_mean = (sum(g["win"] * g["games"] for g in global_augs.values() if g.get("games")) / total_games
                   if total_games else None)

    results = []
    for idx, aid in enumerate(slots):
        r: dict = {"index": idx, "augment_id": aid, "score": None, "basis": None,
                   "verdict": "unknown", "reasons": []}
        if not aid:
            r["reasons"].append("未识别")
            results.append(r)
            continue
        meta = store.augments.get(aid, {})
        r.update(name=meta.get("name", aid), rarity=meta.get("rarity"), icon=meta.get("icon"),
                 desc=meta.get("desc", ""))
        if aid in excluded:
            r.update(verdict="nodata", basis="excluded")
            r["reasons"].append(NODATA_NOTE)
            results.append(r)
            continue
        t = official.get(aid)
        if t:
            r.update(tier=t.get("tier"), pick=t.get("pick"))
        if aid in ranks_by_win:
            r.update(ranks_by_win[aid])
            r["reasons"].append(f"本英雄{RARITY_CN.get(r.get('rarity'), '')}胜率第{r['rank']}/{r['rank_total']}")
        elif t and t.get("rank") and t.get("tier"):
            rarity = t.get("rarity") or meta.get("rarity")
            r.update(rank=t["rank"], rank_total=rarity_total.get(rarity))
            r["reasons"].append(f"官方{RARITY_CN.get(rarity, '')}第{t['rank']}/{rarity_total.get(rarity, '?')}"
                                f"（{t['tier']}级）")

        # 1) 本英雄胜率
        a = ag_augs.get(aid)
        if a and a.get("win") is not None and a.get("games") and base is not None:
            d = smoothed_delta(a["win"], a["games"], base, k)
            r.update(win=a["win"], games=a["games"], sample=sample_level(a["games"]), source="public")
            r["reasons"].append(f"本英雄胜率 {a['win'] * 100:.1f}%（{a['games']}局，{SAMPLE_CN[r['sample']]}，"
                                f"{label}），平滑后较英雄基准 {d:+.1f}pp")
            r["score"] = round(d, 2)
            r["basis"] = "hero_win"
        # 2) 未识别英雄：所有英雄合计的胜率
        if r["score"] is None and not hero_id:
            g = global_augs.get(aid)
            if g and g.get("win") and global_mean:
                d = (g["win"] - global_mean) * 100
                r.update(win=g["win"], games=g.get("games"), score=round(d, 2), basis="global_win")
                r["reasons"].append(f"全英雄胜率 {g['win'] * 100:.1f}%，较平均 {d:+.1f}pp")
        # 3) 只有官方层级
        if r["score"] is None and r.get("tier") in TIER_PP:
            r["score"] = TIER_PP[r["tier"]]
            r["basis"] = "tier_only"
            r["reasons"].append("没有胜率数据，按官方层级估计，可信度低")
        if r["score"] is None:
            if hero_id and (official or ag):
                r["score"] = -3.0
                r["basis"] = "rare"
                r["reasons"].append("该英雄几乎不选，数据不足")
            else:
                r["reasons"].append("暂无可靠数据")
        results.append(r)

    scored = [r for r in results if r["score"] is not None]
    hero_games = [r.get("games") or 0 for r in scored if r.get("basis") == "hero_win"]
    most = max(hero_games, default=0)
    for r in scored:
        if r.get("basis") != "hero_win":
            continue
        g = r.get("games") or 0
        if most >= RELIABLE_GAMES and g < most * TOO_FEW_RATIO:
            r["thin"] = "too_few"
            r["reasons"].append(f"只有 {g} 局，比同屏其他海克斯少一个数量级以上，胜率不可信")
        elif most >= RELIABLE_GAMES and g < RELIABLE_GAMES:
            r["thin"] = "few"
            r["reasons"].append(f"只有 {g} 局，胜率偶然性大，不作首选")

    pool = [r for r in scored if not r.get("thin")] or scored
    best = max(pool, key=lambda r: r["score"]) if pool else None
    passed = [r for r in scored if r is not best and r.get("thin") and best is not None and r["score"] > best["score"]]
    for r in scored:
        if r is best:
            r["verdict"] = "best"
        elif r.get("thin") == "too_few":
            r["verdict"] = "reroll"
        elif r.get("thin"):
            r["verdict"] = "ok"
        elif r["score"] < REROLL_BELOW or r["score"] <= best["score"] - REROLL_GAP:
            r["verdict"] = "reroll"
        else:
            r["verdict"] = "ok"

    if best is None:
        advice = "暂无可靠数据，请自行判断"
    elif len(scored) == len(slots) and all(r["score"] < REROLL_BELOW for r in scored):
        advice = f"三张都不理想，可考虑刷新；非要选就拿「{best.get('name')}」"
    else:
        advice = f"首选「{best.get('name')}」"
        if passed:
            advice += "；" + "、".join(f"「{r.get('name')}」胜率虽高但" +
                                      {"too_few": "样本太少，不可信"}.get(r["thin"], "样本偏少") for r in passed)
    if best is not None and best.get("basis") == "tier_only":
        advice += "（没有胜率数据，按官方层级）"
    if not hero_id:
        advice += "（未识别英雄，按全英雄数据）"

    return {
        "hero_id": hero_id,
        "base_win": base,
        "slots": results,
        "best_index": best["index"] if best else None,
        "advice": advice,
        "sources": {"tencent_date": (hero or {}).get("date"), "win_source": label,
                    "data_patch": (ag or {}).get("patch"), "patch": store.static.get("patch")},
    }
