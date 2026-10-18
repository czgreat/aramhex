from lolhex import recommend
from lolhex.panel import hero_panel


def test_win_rank_uses_win_rate_and_separates_rarity():
    stats = {"a": {"win": .7, "games": 10, "pick": .01},
             "b": {"win": .6, "games": 50000, "pick": .9},
             "c": {"win": .7, "games": 200}, "d": {"win": 0, "games": 30},
             "s": {"win": .8, "games": 100}, "missing": {"win": None, "games": 100}}
    static = {k: {"rarity": "silver" if k == "s" else "prismatic"} for k in stats}
    ranked = recommend.win_ranks(stats, static)
    assert ranked["a"]["rank"] == ranked["c"]["rank"] == 1
    assert ranked["b"]["rank"] == 3 and ranked["d"]["rank"] == 4
    assert ranked["a"]["rank_total"] == 4
    assert ranked["s"]["rank"] == ranked["s"]["rank_total"] == 1
    assert "missing" not in ranked


def test_smoothing_pulls_small_samples_to_baseline():
    big = recommend.smoothed_delta(0.60, 20000, 0.50, 300)
    small = recommend.smoothed_delta(0.80, 20, 0.50, 300)
    assert 9.5 < big < 10
    assert small < 2.0  # 20 局 80% 胜率不应压过 2 万局 60%
    assert recommend.smoothed_delta(None, 100, 0.5, 300) is None


def test_win_rate_beats_official_tier(store):
    # 腾讯把炽燃利息排黄金第 1（按层级/选取），但提莫带虚幻武器的胜率明显更高。
    ev = recommend.evaluate(store, "17", ["2128", "1029", "1373"])
    names = [s["name"] for s in ev["slots"]]
    assert names == ["炽燃利息", "虚幻武器", "缩小引擎"]
    assert ev["best_index"] == 1
    assert ev["slots"][1]["verdict"] == "best" and ev["slots"][1]["basis"] == "hero_win"
    assert ev["slots"][0]["tier"] == "S"
    assert ev["slots"][1]["sample"] == "high" and ev["slots"][1]["source"] == "public"
    assert "首选「虚幻武器」" in ev["advice"]
    assert any("自采统计·16.20版本" in x for x in ev["slots"][1]["reasons"])


def test_unknown_slot_and_rare_augment(store):
    store.ranks["augments"]["1001"] = {"win": 0.5, "games": 5000}  # 别的英雄拿过，这个英雄几乎不选
    ev = recommend.evaluate(store, "17", ["1029", None, "1001"])
    assert ev["slots"][1]["verdict"] == "unknown" and ev["slots"][1]["score"] is None
    assert ev["slots"][2]["basis"] in ("rare", "tier_only")
    assert ev["best_index"] == 0


def test_without_hero_uses_all_heroes_win_rate(store):
    ev = recommend.evaluate(store, None, ["2128", "1029", "1373"])
    assert all(s["basis"] == "global_win" for s in ev["slots"])
    assert ev["best_index"] == 1
    assert "未识别英雄" in ev["advice"]


def test_all_below_baseline_suggests_reroll(store):
    heroes, augs = store.dataset.table(None)
    for aid in ("2128", "1029", "1373"):
        augs["17"][aid] = {"games": 50000, "wins": 22500, "win_rate": 0.45}
    ev = recommend.evaluate(store, "17", ["2128", "1029", "1373"])
    assert all(s["verdict"] in ("best", "reroll") for s in ev["slots"])
    assert ev["advice"].startswith("三张都不理想")


def test_thin_sample_is_never_first_choice(store):
    heroes, augs = store.dataset.table(None)
    base = heroes["17"]["win_rate"]
    augs["17"]["1029"] = {"games": 14281, "wins": round(14281 * (base - 0.006)), "win_rate": base - 0.006}
    augs["17"]["2128"] = {"games": 385, "wins": round(385 * (base + 0.03)), "win_rate": base + 0.03}
    ev = recommend.evaluate(store, "17", ["1029", "2128", None])
    assert ev["best_index"] == 0
    thin = ev["slots"][1]
    assert thin["score"] > ev["slots"][0]["score"]
    assert thin["thin"] == "too_few" and thin["verdict"] == "reroll"
    assert "样本太少" in ev["advice"]


def test_band_changes_hero_baseline_and_augment_win(store):
    store.set_band("top")
    ev = recommend.evaluate(store, "17", ["1029", None, None])
    assert ev["slots"][0]["games"] == 500 and abs(ev["slots"][0]["win"] - 0.59) < 1e-6
    assert any("顶尖" in x for x in ev["slots"][0]["reasons"])
    store.set_band(None)
    assert recommend.evaluate(store, "17", ["1029", None, None])["slots"][0]["games"] == 40000


def test_sample_levels():
    assert recommend.sample_level(None) == "none"
    assert recommend.sample_level(100) == "low"
    assert recommend.sample_level(500) == "medium"
    assert recommend.sample_level(5000) == "high"


def test_mayhem_dictionary_excludes_arena_only(store):
    store.static["augments"]["99999"] = {"id": "99999", "name": "仅竞技场", "rarity": "gold", "modes": ["CHERRY"]}
    assert "99999" not in store.mayhem_augments()
    assert "1029" in store.mayhem_augments()


def test_resolve_hero_variants(store):
    for key in ("17", "Teemo", "提莫", "迅捷斥候", "game_character_displayname_Teemo", 17):
        assert store.resolve_hero(key) == "17"
    assert store.resolve_hero("不存在") is None


def test_hero_panel_merges_official_build_with_stats(store):
    p = hero_panel(store, "17")
    assert p["win_source"] == "自采统计·16.20版本"
    by_id = {a["id"]: a for a in p["augments"]}
    assert by_id["1029"]["win"] == 0.6 and by_id["1029"]["delta"] > 0 and by_id["1029"]["source"] == "public"
    assert by_id["2128"]["tier"] == "S"  # 官方层级保留
    only_official = [a for a in p["augments"] if a["source"] is None]
    assert only_official and all(a.get("tier") for a in only_official)  # 统计里没有的：只有官方层级
    assert p["builds"]["core"] and p["skills"]
    assert p["rank"]["rank"] == 1 and p["rank"]["total"] == 2


def test_excluded_augment_has_no_data(store):
    # 数据集 meta.json 的 excluded_augments（如尊我为王）：不打分、不按官方层级兜底、不当首选
    store.dataset.meta["excluded_augments"] = {"1373": {"name": "缩小引擎", "reason": "测试"}}
    assert store.excluded_augments() == {"1373": "测试"}
    ev = recommend.evaluate(store, "17", ["1373", "1029", "2128"])
    s = ev["slots"][0]
    assert s["verdict"] == "nodata" and s["score"] is None and s["basis"] == "excluded"
    assert s["name"] == "缩小引擎" and recommend.NODATA_NOTE in s["reasons"]
    assert "tier" not in s
    assert ev["best_index"] == 1 and "缩小引擎" not in ev["advice"]



def test_new_augment_without_stats_has_no_data(store):
    # 测试服 / 刚上线的新海克斯：统计里没有时标「无数据」，不当成「几乎不选」扣分、提示刷新
    store.augments["9999"] = {"id": "9999", "name": "测试新海克斯", "rarity": "gold", "new": True}
    ev = recommend.evaluate(store, "17", ["9999", "1029", "2128"])
    s = ev["slots"][0]
    assert s["verdict"] == "nodata" and s["basis"] == "new" and s["score"] is None
    assert recommend.NEW_NOTE in s["reasons"]
    assert ev["best_index"] != 0
    # 有统计的海克斯即使标了新，也照常按胜率算
    store.augments["1029"] = {**store.augments.get("1029", {}), "new": True}
    ev = recommend.evaluate(store, "17", ["9999", "1029", "2128"])
    assert ev["slots"][1]["basis"] == "hero_win"


def test_augment_missing_from_all_stats_is_new_even_without_flag(store):
    # 4.0 上线后腾讯可能去掉 isPBE 标记：整份统计里一局都没有的海克斯照样标「无数据」，不提示刷新
    store.augments["9998"] = {"id": "9998", "name": "上线后的新海克斯", "rarity": "gold"}
    ev = recommend.evaluate(store, "17", ["9998", "1029", "2128"])
    s = ev["slots"][0]
    assert s["verdict"] == "nodata" and s["basis"] == "new" and s["score"] is None


def test_provisional_augment_uses_pbe_total_and_skips_thin_rule(store):
    # 过渡数据：3086 是测试服的新海克斯，合计 5000 人次 56%，提莫只有 40 局。
    # 不能因为局数比同屏老海克斯少一个数量级就判「样本太少、刷新」，要按合计胜率打底
    store.dataset.meta["provisional_augments"] = {"3086": {"games": 5000, "wins": 2800, "win_rate": 0.56,
                                                           "source": "测试服 16.21.828"}}
    store.dataset.table(None)[1]["17"]["3086"] = {"games": 40, "wins": 24, "win_rate": 0.6, "low_sample": True}
    store.augments["3086"] = {"id": "3086", "name": "小蜜蜂！！！", "rarity": "gold", "new": True}
    ev = recommend.evaluate(store, "17", ["3086", "2128", "1029"])
    s = ev["slots"][0]
    assert s["basis"] == "pbe" and not s.get("thin") and s["verdict"] in ("ok", "best")
    assert 4.0 < s["score"] < 7.0  # 合计 +6pp 打底，本英雄 40 局 60% 微调
    assert any("测试服" in x for x in s["reasons"])
    assert ev["slots"][1]["verdict"] == "reroll" and ev["best_index"] == 2
    # 本英雄在测试服一局没有：只按合计
    del store.dataset.table(None)[1]["17"]["3086"]
    s = recommend.evaluate(store, "17", ["3086", "2128", "1029"])["slots"][0]
    assert s["basis"] == "pbe" and s["games"] is None and 5.0 < s["score"] < 6.0
