"""1.1.7：选了前 5% 时核心三件、命运之武器首件装备用前 5% 的；前 5% 没有就用全体并标「全体」。"""

from lolhex import panel
from lolhex.data.public import Dataset
from lolhex.ui import buildview
from test_public import _add_file

ALL_CORE = [{"items": ["3118", "4645", "3089"], "ordered": True, "games": 45637, "wins": 23469,
             "win": 23469 / 45637, "pick": 0.07}]
HIGH_CORE = {"17": [
    {"items": ["3118", "6653", "3116"], "ordered": True, "games": 2444, "wins": 1286, "pick": 0.058},
    {"items": ["3118", "3118", "3116"], "ordered": True, "games": 5000, "wins": 2500, "pick": 0.1},  # 重复装备
    {"items": ["3118", "4645"], "ordered": True, "games": 5000, "wins": 2500, "pick": 0.1},          # 不是三件
    {"items": ["3118", {}, "3116"], "ordered": True, "games": 5000, "wins": 2500, "pick": 0.1},      # ID 不是字符串（1.1.9）
    {"items": ["3118", "4645", "3089"], "ordered": False, "games": 5000, "wins": 2500, "pick": 0.1}, # 无序
    {"items": ["3118", "4645", "3089"], "ordered": True, "games": 10, "wins": 11, "pick": 0.1},      # 胜场超局数
    {"items": ["3118", "4645", "6653"], "ordered": True, "games": 5854, "wins": 2936, "pick": 0.139}]}
DESTINY_ALL = {"17": {"3081": {"source": "stats", "total": 1000, "items": [
    {"item": "4645", "games": 300, "wins": 160}]}}}
DESTINY_HIGH = {"17": {"3081": {"source": "stats", "total": 100, "items": [
    {"item": "3135", "games": 40, "wins": 25}]}},
    "1": {"3081": {"source": "stats", "total": 50, "items": [], "note": "样本太少，暂无装备参考"}}}


def _with_files(store, files):
    for name, obj in files.items():
        _add_file(store.dataset.folder, name, obj)
    store.dataset = Dataset(store.dataset.folder, store.dataset.origin)


def _official_core(store, monkeypatch):
    real = store.hero

    def hero(hid, allow_network=True):
        data = real(hid, allow_network) or {}
        return {**data, "builds": {**(data.get("builds") or {}), "core": ALL_CORE}}
    monkeypatch.setattr(store, "hero", hero)


def test_core_builds_only_for_high_band_and_valid_rows(store):
    _with_files(store, {"core_builds.json": {"17": ALL_CORE}, "bands/high/core_builds.json": HIGH_CORE})
    assert store.core_builds("17") is None  # 没选分段：用官方资料包
    store.set_band("mid")
    assert store.core_builds("17") is None  # 中分没有单独的装备推荐
    store.set_band("high")
    rows = store.core_builds("17")
    assert [r["items"] for r in rows] == [["3118", "6653", "3116"], ["3118", "4645", "6653"]]  # 坏行跳过，顺序不变
    assert rows[0]["win"] == 1286 / 2444 and rows[0]["pick"] == 0.058 and rows[0]["ordered"] is True
    assert store.core_builds("1") is None  # 前 5% 没有这个英雄：退回全体
    store.set_band("top")  # 老设置里的 top 当 high
    assert store.core_builds("17")


def test_core_builds_without_band_file_falls_back(store):
    _with_files(store, {"core_builds.json": {"17": ALL_CORE}})  # 老数据：只有全体
    store.set_band("high")
    assert store.core_builds("17") is None


def test_panel_uses_high_core_and_labels_fallback(store, monkeypatch):
    _official_core(store, monkeypatch)
    _with_files(store, {"bands/high/core_builds.json": {"17": HIGH_CORE["17"][:1]}})
    d = panel.hero_panel(store, "17")
    assert [x["id"] for x in d["builds"]["core"][0]["items"]] == ["3118", "4645", "3089"]
    assert d["core_band"] is None  # 没选分段不标
    store.set_band("high")
    d = panel.hero_panel(store, "17")
    assert [x["id"] for x in d["builds"]["core"][0]["items"]] == ["3118", "6653", "3116"]
    assert d["core_band"] == "前 5%"
    assert "前 5%" in "".join(buildview.lines(d, None, True))
    _with_files(store, {"bands/high/core_builds.json": {"17": []}})  # 前 5% 留空：用全体，写「全体」
    d = panel.hero_panel(store, "17")
    assert [x["id"] for x in d["builds"]["core"][0]["items"]] == ["3118", "4645", "3089"]
    assert d["core_band"] == "全体"
    store.set_band("casual")
    assert panel.hero_panel(store, "17")["core_band"] == "全体"


def test_panel_without_any_core_has_no_label(store, monkeypatch):
    real = store.hero
    monkeypatch.setattr(store, "hero", lambda hid, allow_network=True: {
        **(real(hid, allow_network) or {}), "builds": {"core": []}})
    _with_files(store, {"bands/high/core_builds.json": {"17": []}})
    store.set_band("high")
    d = panel.hero_panel(store, "17")  # 佛耶戈这类全体也没有三件套的：不显示，也不标
    assert d["builds"]["core"] == [] and d["core_band"] is None
    assert not any("核心三件" in x for x in buildview.lines(d, None, True))


def test_destiny_items_use_high_band_then_all(store):
    _with_files(store, {"hero_augment_items.json": DESTINY_ALL, "bands/high/hero_augment_items.json": DESTINY_HIGH})
    hint = store.augment_items("17", "3081")
    assert [r["item"] for r in hint["items"]] == ["4645"] and "band_label" not in hint
    store.set_band("high")
    hint = store.augment_items("17", "3081")
    assert [r["item"] for r in hint["items"]] == ["3135"] and hint["band_label"] == "前 5%"
    assert store.augment_items("1", "3081") is None  # 前 5% 只有一句说明、全体也没有：不显示
    _with_files(store, {"hero_augment_items.json": {**DESTINY_ALL, "1": DESTINY_ALL["17"]}})
    hint = store.augment_items("1", "3081")
    assert [r["item"] for r in hint["items"]] == ["4645"] and hint["band_label"] == "全体"
    store.set_band("mid")
    assert store.augment_items("17", "3081")["band_label"] == "全体"


def test_destiny_hint_title_shows_band():
    from lolhex.ui.overlay import hint_title
    assert hint_title({"items": []}) == "首件传说装备"
    assert hint_title({"band_label": "前 5%"}) == "首件传说装备 · 前 5%"
