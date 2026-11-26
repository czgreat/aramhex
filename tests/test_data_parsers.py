from conftest import fixture

from lolhex.data import tencent


def test_hero_hex_augments_have_rarity_rank_and_tier():
    d = tencent.parse_hero_hex(fixture("tencent_hero17.json"))
    assert d["date"] == "20260924"
    by_id = {a["id"]: a for a in d["augments"]}
    burn = by_id["2128"]  # 炽燃利息：提莫黄金第 1，总榜第 1
    assert burn["rarity"] == "gold" and burn["rank"] == 1 and burn["tier"] == "S"
    assert burn["overall_rank"] == 1 and abs(burn["pick"] - 0.2546) < 1e-6
    rarities = {a["rarity"] for a in d["augments"]}
    assert rarities == {"silver", "gold", "prismatic"}
    # 每个稀有度内名次从 1 连续编号
    for r in rarities:
        ranks = sorted(a["rank"] for a in d["augments"] if a["rarity"] == r)
        assert ranks == list(range(1, len(ranks) + 1))


def test_hero_hex_builds_and_skills():
    d = tencent.parse_hero_hex(fixture("tencent_hero17.json"))
    b = d["builds"]
    assert len(b["full"][0]["items"]) == 6 and 0 < b["full"][0]["win"] < 1
    assert b["core"][0]["items"] == ["3118", "6653", "4645"] and abs(b["core"][0]["win"] - 0.499) < 1e-6
    assert b["shoes"][0]["item"] == "3020"
    assert b["start"][0]["items"] == ["1042", "3113"]
    assert b["first"][0]["item"] == "6653" and abs(b["first"][0]["pick"] - 0.8267) < 1e-6  # showrate 是出场率
    assert 0 < b["core"][0]["pick"] < 1
    s = d["skills"][0]
    assert s["max"] == ["Q", "E", "W"]
    assert len(s["sequences"][0]["seq"]) == 15 and s["sequences"][0]["seq"][5] == "R"
    assert d["partners"][0]["hero"] == "157"


def test_hero_hex_skips_empty_shoes_and_starting_items():
    raw = {"code": 0, "data": {"_fieldValues": {"test": {
        "itemshoes": "$0.1$0.5#,, $0.2$0.5# 3020 $0.3$0.6",
        "itemout": ",,$0.1$0.5#1042, ,3113$0.2$0.6",
    }}}}
    builds = tencent.parse_hero_hex(raw)["builds"]
    assert builds["shoes"] == [{"item": "3020", "pick": 0.3, "win": 0.6}]
    assert builds["start"] == [{"items": ["1042", "3113"], "pick": 0.2, "win": 0.6}]


def test_hero_and_augment_rank():
    _, heroes = tencent.parse_hero_rank(fixture("tencent_hero_rank.json"))
    assert heroes["157"]["rank"] == 1 and 0.5 < heroes["157"]["win"] < 0.7
    assert heroes["17"]["top_augments"][:1] == ["2128"]
    date, augs = tencent.parse_augment_rank(fixture("tencent_augment_rank.json"))
    assert date == "20260924"
    assert augs["1001"]["win"] == 0.4783 and augs["1001"]["win_rank"] == 188


def test_static_parsers():
    heroes = tencent.parse_hero_list(fixture("tencent_hero_list.json"))
    assert heroes["17"]["name"] == "提莫" and heroes["17"]["nick"] == "迅捷斥候" and heroes["17"]["alias"] == "Teemo"
    augs = tencent.parse_kiwi_augments(fixture("tencent_kiwi_augments.json"))
    assert augs["1001"]["name"] == "泰坦的坚决" and augs["1001"]["rarity"] == "prismatic"
    assert "<" not in augs["1001"]["desc"]
    arena = tencent.parse_kiwi_augments([{"augmentID": 9, "name_cn": "x", "mode": "CHERRY, KIWI", "level": "kGold"}])
    assert arena["9"]["modes"] == ["CHERRY", "KIWI"]
    spells = tencent.parse_hero_spells(fixture("tencent_hero17_spells.json"))
    assert spells["Q"]["name"] == "致盲吹箭"



def test_odp_error_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        tencent.parse_hero_hex({"code": 500})
