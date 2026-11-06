"""人工更正（official_overrides.json）：重新抓取时不丢，同名海克斯出不了候选。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from official_data import OVERRIDES as OFFICIAL_OVERRIDES, _check_unique_names, apply_overrides, candidate_report, create_candidate  # noqa: E402

from lolhex.data.icons import BUNDLED as ICONS  # noqa: E402
from lolhex.data.store import BUNDLED_HEROES, BUNDLED_STATIC  # noqa: E402
from lolhex.vision.matcher import NameMatcher  # noqa: E402

OVERRIDES = {
    "augments_remove": ["393"],
    "augments": {"1361": {"desc": "人工", "new": True}, "2126": {"name": "装填", "rarity": "gold", "modes": ["KIWI"]}},
    "items_exclude": {"3042": {"name": "魔切", "ask_user": True}},
}


def _static():
    return {"augments": {"393": {"id": "393", "name": "艾卡西亚的陷落"},
                         "1361": {"id": "1361", "name": "艾卡西亚的陷落", "desc": ""}},
            "items": {"1001": {"id": "1001", "name": "鞋子"}, "3042": {"id": "3042", "name": "魔切"}}}


def test_overrides_remove_add_and_fill():
    s = apply_overrides(_static(), OVERRIDES)
    assert "393" not in s["augments"]
    assert s["augments"]["1361"]["desc"] == "人工" and s["augments"]["1361"]["new"] is True
    assert s["augments"]["2126"] == {"id": "2126", "name": "装填", "rarity": "gold", "modes": ["KIWI"]}
    assert set(s["items"]) == {"1001"}  # 腾讯以后列出来也不放，要放得先问用户


def test_upstream_wins_except_new():
    """腾讯以后补上了说明就用腾讯的；new 是否算新由我们定。"""
    s = _static()
    s["augments"]["1361"].update(desc="腾讯", new=False)
    apply_overrides(s, OVERRIDES)
    assert s["augments"]["1361"]["desc"] == "腾讯" and s["augments"]["1361"]["new"] is True


def test_same_name_is_rejected_but_bang_variant_is_allowed():
    with pytest.raises(ValueError, match="393"):
        _check_unique_names({"augments": _static()["augments"]})
    _check_unique_names({"augments": {"1": {"name": "罪恶快感"}, "2": {"name": "罪恶快感！"},
                                      "3": {"name": "连锁反应", "modes": ["CHERRY"]},
                                      "4": {"name": "连锁反应", "modes": ["KIWI"]}}})


def test_candidate_rejects_duplicate_names(tmp_path):
    static = json.loads(BUNDLED_STATIC.read_text(encoding="utf-8"))
    static["augments"]["393"] = {"id": "393", "name": "艾卡西亚的陷落", "rarity": "prismatic", "modes": ["KIWI"]}
    path = tmp_path / "static.json"
    path.write_text(json.dumps(static, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="393"):
        create_candidate(path, BUNDLED_HEROES, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_bundled_has_the_overrides_applied():
    static = json.loads(BUNDLED_STATIC.read_text(encoding="utf-8"))
    _check_unique_names(static)
    augments, items = static["augments"], static["items"]
    assert "393" not in augments and "1064" not in augments
    assert NameMatcher({k: v["name"] for k, v in augments.items()}).exact("艾卡西亚的陷落") == "1361"
    for aid in ("1361", "2119"):
        assert augments[aid]["new"] is True and augments[aid]["desc"] and augments[aid]["icon"]
    assert all(augments[aid]["desc"] and "@" not in augments[aid]["desc"] for aid in ("2126", "2140", "2143", "1343", "2109"))
    excluded = json.loads(OFFICIAL_OVERRIDES.read_text(encoding="utf-8"))["items_exclude"]
    assert set(excluded) == {"3040", "3042", "3121", "2530", "2052", "220013", "3400"}
    assert sorted(k for k, v in excluded.items() if v.get("ask_user")) == ["2530", "3040", "3042", "3121"]
    icons = json.loads(ICONS.read_text(encoding="utf-8"))["icons"]["item"]
    assert not set(excluded) & set(items) and not set(excluded) & set(icons)


def test_report_reminds_to_ask_about_tear_upgrades(tmp_path):
    create_candidate(BUNDLED_STATIC, BUNDLED_HEROES, tmp_path / "out")
    assert candidate_report(tmp_path / "out")["items_ask_user"] == ["末日寒冬", "歌之权冠", "炽天使之拥", "魔切"]
