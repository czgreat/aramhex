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


@pytest.mark.parametrize("position", ["top", "bottom"])
@pytest.mark.parametrize("height", [1080, 1440, 2160])
def test_item_hint_opposite_side_icons_and_mask(position, height, tmp_path, monkeypatch):
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
                "item_hint": {"source": "community", "items": [
                    {"item": "3135", "name": "虚空之杖", "win": .55, "games": 50},
                    {"item": "4645", "name": "影焰", "win": None}]}}]}}
    layer.update_from(snap, window.Rect(0, 0, int(1920*k), height))
    app.processEvents()
    image = QImage(int(1920*k), height, QImage.Format_ARGB32)
    image.fill(0)
    layer.render(image)
    base, hint = [r for r in published if len(r) == 2][-1]
    assert (base.top < 430*k) == (position == "top")
    assert (hint.top < 430*k) == (position == "bottom")
    assert hint.top >= 0 and hint.top + hint.height <= height
    assert ("item", "3135") in calls and ("item", "4645") in calls
    # 不只检查加载调用：图标确实画到了推荐面板里（含 2K 的非整数缩放）。
    assert image.pixelColor(int(hint.left + 2 + 20*k), int(hint.top + 2 + 35*k)) == QColor("yellow")
    assert not image.isNull()
    layer.close()
