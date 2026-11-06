from conftest import render_in_game
from lolhex import recommend
from lolhex.data.public import Dataset
from test_public import _add_file
import pytest


def test_custom_equipment_optional_rates_order_and_validation(store):
    assert store.augment_items("17", "3081") is None
    cfg = {"17": {"3081": {"source": "community", "show_winrate": False, "items": [
        {"item": "3135", "name": "虚空之杖", "games": 2, "wins": 2},
        {"item": "4645", "name": "影焰"},
        {"item": "3135", "name": "重复"},
        {"item": "bad", "name": "无效"},
        {"item": "999", "name": "坏统计", "games": True, "wins": 1}]}}}
    _add_file(store.dataset.folder, "hero_augment_items.json", cfg)
    store.dataset = Dataset(store.dataset.folder, store.dataset.origin)
    rows = store.augment_items("17", "3081")
    assert rows["source"] == "community"
    assert [r["item"] for r in rows["items"]] == ["3135", "4645"]
    assert all(r["win"] is None for r in rows["items"])
    assert store.augment_items("1", "3081") is None
    cfg["17"]["3081"].update(source="stats", show_winrate=True)
    _add_file(store.dataset.folder, "hero_augment_items.json", cfg)
    store.dataset = Dataset(store.dataset.folder, store.dataset.origin)
    assert store.augment_items("17", "3081")["items"][0]["win"] == 1.0


def test_equipment_only_for_recognized_hero_and_destiny_card(store, monkeypatch):
    hint = {"source": "manual", "items": [{"item": "3135", "name": "虚空之杖", "win": None}]}
    monkeypatch.setattr(store, "augment_items", lambda hid, aid: hint)
    store.augments["3081"] = {"id": "3081", "name": "命运之武器", "rarity": "gold"}
    ev = recommend.evaluate(store, "17", ["3081", "1029", "2128"])
    assert ev["slots"][0]["item_hint"] == hint
    assert "item_hint" not in ev["slots"][1]
    assert "item_hint" not in recommend.evaluate(store, None, ["3081"])["slots"][0]


def test_pbe_small_sample_display_preserves_scoring(store):
    store.dataset.meta["provisional_augments"] = {"3086": {"games": 5000, "wins": 2800, "win_rate": .56}}
    store.augments["3086"] = {"id": "3086", "name": "小蜜蜂", "rarity": "gold"}
    rows = store.dataset.table(None)[1]["17"]
    rows["3086"] = {"games": 2, "wins": 2, "win_rate": 1.0}
    r = recommend.evaluate(store, "17", ["3086"])["slots"][0]
    assert r["win"] == 1.0 and r["games"] == 2  # 原始数据和推荐算法保持
    assert recommend.win_text(r) == "全英雄胜率56.0% · 本英雄2局"
    assert 0 < r["score"] < 10
    rows["3086"] = {"games": 30, "wins": 18, "win_rate": .6}
    r = recommend.evaluate(store, "17", ["3086"])["slots"][0]
    assert recommend.win_text(r) == "胜率60.0%"


def test_live_new_augment_small_sample_uses_labelled_global_rate(store):
    store.augments["1029"]["new"] = True
    rows = store.dataset.table(None)[1]["17"]
    rows["1029"] = {"games": 2, "wins": 2, "win_rate": 1.0}
    r = recommend.evaluate(store, "17", ["1029"])["slots"][0]
    assert r["basis"] == "hero_win" and r["win"] == 1.0
    assert r["display_win_scope"] == "global"
    assert recommend.win_text(r).startswith("全英雄胜率")
    assert "本英雄2局" in recommend.win_text(r)


def test_equipment_rows_keep_file_order_and_show_pick_low_sample_note(store):
    cfg = {"17": {"3081": {"source": "stats", "total": 1000, "items": [
        {"item": "3135", "games": 20, "wins": 10, "low_sample": True},
        {"item": "4645", "games": 500, "wins": 300}]}}}
    _add_file(store.dataset.folder, "hero_augment_items.json", cfg)
    store.dataset = Dataset(store.dataset.folder, store.dataset.origin)
    rows = store.augment_items("17", "3081")["items"]
    assert [r["item"] for r in rows] == ["3135", "4645"]  # 胜率低的写在前面就排前面：不重排
    assert rows[0]["pick"] == 0.02 and rows[0]["low_sample"] is True and rows[0]["win"] == 0.5
    assert rows[1]["pick"] == 0.5 and rows[1]["low_sample"] is False
    cfg["17"]["3081"].update(items=[], note="样本太少，暂无装备参考")
    _add_file(store.dataset.folder, "hero_augment_items.json", cfg)
    store.dataset = Dataset(store.dataset.folder, store.dataset.origin)
    hint = store.augment_items("17", "3081")
    assert hint["items"] == [] and hint["note"] == "样本太少，暂无装备参考"
    cfg["17"]["3081"].pop("note")
    _add_file(store.dataset.folder, "hero_augment_items.json", cfg)
    store.dataset = Dataset(store.dataset.folder, store.dataset.origin)
    assert store.augment_items("17", "3081") is None


def test_destiny_baseline_uses_raw_rates_and_keeps_source_order(store):
    cfg = {"17": {"3081": {"source": "stats", "total": 10000,
        "baseline": {"games": 10000, "wins": 5200}, "items": [
            {"item": "3135", "games": 2000, "wins": 1000},
            {"item": "4645", "games": 2000, "wins": 1140}]}}}
    _add_file(store.dataset.folder, "hero_augment_items.json", cfg)
    store.dataset = Dataset(store.dataset.folder, store.dataset.origin)
    hint = store.augment_items("17", "3081")
    assert not hint["show_sample_size"] and not hint["show_pick_rate"]
    assert [r["item"] for r in hint["items"]] == ["3135", "4645"]
    assert [r["win"] for r in hint["items"]] == [.5, .57]
    assert [r["delta"] for r in hint["items"]] == pytest.approx([-2, 5])
    # 原海克斯的平滑强度不影响命运装备原始胜率或差值。
    for k in (0, 300, 10000):
        ev = recommend.evaluate(store, "17", ["3081"], k=k)
        assert ev["slots"][0]["item_hint"] == hint


@pytest.mark.parametrize("baseline", [None, {}, {"games": 0, "wins": 0},
    {"games": True, "wins": 1}, {"games": 10000, "wins": 10001},
    {"games": 10000, "wins": float("nan")},
    {"games": 100, "wins": 52},  # 基准不能比这条装备样本更小。
    {"games": 10000, "wins": 500},  # 装备胜场不能多于同口径基准胜场。
    {"games": 10000, "wins": 9500}])  # 负场也必须满足子集关系。
def test_bad_or_missing_destiny_baseline_keeps_winrate_without_inventing_delta(store, baseline):
    cfg = {"17": {"3081": {"source": "stats", "baseline": baseline,
        "items": [{"item": "4645", "games": 2000, "wins": 1140}]}}}
    _add_file(store.dataset.folder, "hero_augment_items.json", cfg)
    store.dataset = Dataset(store.dataset.folder, store.dataset.origin)
    row = store.augment_items("17", "3081")["items"][0]
    assert row["win"] == .57 and row["delta"] is None


def test_destiny_display_controls_do_not_drop_counts_and_respect_hidden_rates(store):
    options = {"source": "stats", "show_sample_size": True, "show_pick_rate": True,
        "baseline": {"games": 10000, "wins": 5200},
        "items": [{"item": "4645", "games": 2000, "wins": 1140}]}
    for change, expected in (({}, .57), ({"show_delta": False}, .57), ({"show_winrate": False}, None)):
        cfg = {"17": {"3081": {**options, **change}}}
        _add_file(store.dataset.folder, "hero_augment_items.json", cfg)
        store.dataset = Dataset(store.dataset.folder, store.dataset.origin)
        hint = store.augment_items("17", "3081")
        assert hint["show_sample_size"] and hint["show_pick_rate"]
        row = hint["items"][0]
        assert row["games"] == 2000 and row["win"] == expected
        assert (row["delta"] is not None) == (not change)


@pytest.mark.parametrize("options,show_win,expected", [
    ({}, True, "胜率 57.0% · +5.0"),
    ({"show_rate": False}, True, "+5.0"),
    ({"show_sample_size": True}, True, "胜率 57.0% · +5.0 · 2,000局"),
    ({"show_pick_rate": True}, True, "胜率 57.0% · +5.0 · 选取 20%"),
    ({}, False, ""),
    ({"show_sample_size": True}, False, "2,000局"),
])
def test_destiny_overlay_renders_only_requested_statistics(options, show_win, expected):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QFont, QFontMetrics
    from PySide6.QtWidgets import QApplication
    from lolhex.ui.overlay import BadgeLayer

    app = QApplication.instance() or QApplication([])
    layer = BadgeLayer()
    class Painter:
        def __init__(self): self.text = []
        def setPen(self, *args): pass
        def setBrush(self, *args): pass
        def drawRoundedRect(self, *args): pass
        def drawText(self, *args): self.text.append(args[-1])
    painter = Painter()
    hint = {**options, "items": [{"item": "4645", "name": "影焰", "win": .57,
                                  "delta": 5, "games": 2000, "pick": .2}]}
    try:
        # 本例检查字段组合，留足宽度以免离屏平台字体/DPI差异触发省略。
        layer._paint_item_hint(painter, hint, QRect(0, 0, 600, 200),
                               QFontMetrics(QFont("Microsoft YaHei UI", 9)), show_win)
        assert painter.text == ["首件传说装备", "影焰", expected]
    finally:
        layer.close()
        app.processEvents()


@pytest.mark.parametrize("position", ["top", "bottom"])
@pytest.mark.parametrize("height", [1080, 1440, 2160])
def test_item_hint_floats_over_empty_space_with_icons_and_mask(position, height, tmp_path, monkeypatch):
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication
    from lolhex.ui.overlay import BadgeLayer
    from lolhex.vision import window
    app = QApplication.instance() or QApplication([])
    icon = QImage(24, 24, QImage.Format_ARGB32)
    icon.fill(QColor("yellow"))
    path = str(tmp_path / "item.png")
    icon.save(path)
    calls = []
    class Icons:
        def local(self, kind, iid, url):
            calls.append((kind, iid))
            return path
    layer = BadgeLayer()
    layer.icons = Icons()
    published = []
    monkeypatch.setattr(window, "set_drawn", lambda key, rects: published.append(rects))
    k = height / 1080
    snap = {"settings": {"badge_position": position},
            "offer": {"boxes": [[int(v*k) for v in [700, 430, 1000, 470]]]},
            "evaluation": {"slots": [{"index": 0, "verdict": "best", "score": 2,
                "item_hint": {"source": "stats", "items": [
                    {"item": "3135", "name": "虚空之杖", "win": .55, "games": 50, "pick": .004, "low_sample": True},
                    {"item": "4645", "name": "影焰", "win": None}]}}]}}
    layer.update_from(snap, window.Rect(0, 0, int(1920*k), height))
    app.processEvents()
    image = QImage(int(1920*k), height, QImage.Format_ARGB32)
    image.setDevicePixelRatio(layer.devicePixelRatioF())
    image.fill(0)
    render_in_game(layer, image)
    base, hint = [r for r in published if len(r) == 2][-1]
    assert hint.top + hint.height <= (430 + 20 + 236)*k  # 不出卡片底边
    if position == "bottom":
        assert hint.top + hint.height <= 430*k + 2  # 标签占了底部，提示放标题上方，不盖标题和说明
        assert hint.top + hint.height <= base.top
    else:
        assert hint.top >= 470*k  # 不盖标题
        assert base.top + base.height <= 470*k
    assert ("item", "3135") in calls and ("item", "4645") in calls
    # 不只检查加载调用：图标确实画到了提示里（含 2K 的非整数缩放）。
    assert image.pixelColor(int(hint.left + 20*k), int(hint.top + 38*k)) == QColor("yellow")
    # 只包住内容：两行的高度，不比卡片还宽；默认位置不碰说明文字（标题下方约 70 基准像素起，短说明到约 90）。
    assert hint.height <= (24 + 2*30 + 4 + 4)*k + 1 and hint.width <= 320*k
    if position == "top":
        assert hint.top >= 580*k
    # 背景半透明：装备图标外的空白处透出下面的画面。
    from lolhex.ui.overlay import HINT_BG
    assert HINT_BG.alpha() < 200
    layer.close()


def test_item_hint_note_only_when_nothing_qualifies(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from lolhex.ui.overlay import BadgeLayer
    from lolhex.vision import window
    app = QApplication.instance() or QApplication([])
    layer = BadgeLayer()
    published = []
    monkeypatch.setattr(window, "set_drawn", lambda key, rects: published.append(rects))
    snap = {"settings": {"badge_position": "top"}, "offer": {"boxes": [[700, 430, 1000, 470]]},
            "evaluation": {"slots": [{"index": 0, "verdict": "best", "score": 2,
                "item_hint": {"source": "stats", "items": [], "note": "样本太少，暂无装备参考"}}]}}
    layer.update_from(snap, window.Rect(0, 0, 1920, 1080))
    app.processEvents()
    layer.grab()
    _, hint = [r for r in published if len(r) == 2][-1]
    assert hint.height <= 34 and hint.top > 600  # 只在底部一行（遮罩四周多留 2 像素），不盖说明
    layer.close()
