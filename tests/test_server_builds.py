"""服务端决定出装推荐和样本筛选；界面区分组合与购买顺序。"""
from copy import deepcopy

import pytest

from lolhex.panel import hero_panel
from lolhex.ui import buildview


def test_panel_preserves_all_server_build_rows_and_order(store, monkeypatch):
    data = deepcopy(store.official_hero("17", allow_network=False))
    combinations = [
        {"items": ["4645", "3135", "3089"], "win": .4, "games": 1, "pick": .00001, "ordered": True},
        {"items": ["3089", "4645", "3135"], "win": .9, "games": 10000, "pick": .9},
        {"items": ["3135", "3089", "4645"]},
        {"items": ["1001", "3135", "4645"], "win": None},
    ]
    singles = [{"item": "1001", "win": .1, "pick": .00001}, {"item": "3006", "win": .9}]
    data["builds"] = {"core": combinations, "full": combinations,
                      "start": combinations, "first": singles, "shoes": singles}
    monkeypatch.setattr(store, "official_hero", lambda *a, **kw: data)
    before = deepcopy(data)
    for band in (None, "high"):
        store.set_band(band)
        panel = hero_panel(store, "17", allow_network=False)
        assert [[i["id"] for i in row["items"]] for row in panel["builds"]["core"]] == [
            row["items"] for row in combinations]
        assert panel["builds"]["core"][0]["ordered"] is True
        assert panel["skills"] == data["skills"]
    assert data == before


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("show_win", [False, True])
def test_core_display_keeps_first_three_and_explicit_purchase_order(compact, show_win):
    def row(names, **stats):
        return {"items": [{"id": str(i), "name": n} for i, n in enumerate(names)], **stats}
    panel = {"builds": {"core": [row(["a", "b", "c"], ordered=True, win=.4),
                                  row(["d", "e", "f"], ordered=False, win=.9),
                                  row(["g", "h", "i"]),
                                  row(["hidden", "fourth", "row"])]}}
    lines = buildview.lines(panel, None, show_win, compact=compact)
    assert len(lines) == 4  # 标题和三套；不足样本已在发布前处理，不在这里补位。
    assert "a → b → c" in lines[1]
    assert "d + e + f" in lines[2]
    assert "g + h + i" in lines[3]
    assert "hidden" not in "".join(lines)
    assert ("40.0%" in lines[1]) is show_win
    assert ("90.0%" in lines[2]) is show_win


def test_core_empty_remains_empty_and_does_not_replace_with_common_items():
    panel = {"builds": {"core": []}, "item_stats": [{"item": {"id": "4645", "name": "影焰"}}]}
    text = "".join(buildview.lines(panel, None, True))
    assert "常出装备" in text
    assert "核心三件" not in text
