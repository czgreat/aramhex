import copy
import json
import threading
from types import SimpleNamespace

import pytest

from lolhex import cloud
from lolhex.config import Settings
from lolhex.data.store import DataStore
from lolhex.data import tencent
from lolhex.engine import Engine, augment_aliases, augment_matcher
from lolhex.vision.detector import AugmentDetector
from lolhex.vision.matcher import NameMatcher


@pytest.fixture(autouse=True)
def clean_cloud(monkeypatch):
    monkeypatch.setattr(cloud, "_cache", cloud._merge({}))


def configure(raw):
    cloud._cache = cloud._merge({"augments": raw})


def test_default_aliases_and_rarity_remain_bundled():
    assert augment_aliases()["1005"] == ["喂鸣喂鸣"]
    assert tencent.rarity_fixes() == tencent.RARITY_FIX


def test_alias_override_and_removal_preserve_primary_names():
    store = SimpleNamespace(mayhem_augments=lambda: {"1005": {"name": "喂呜喂呜"}})
    configure({"aliases": {"1005": ["测试别名"], "9999": ["幽灵条目"]}})
    matcher = augment_matcher(store, Settings())
    assert matcher.match("测试别名") == ("1005", 100.0)
    assert matcher.match("喂呜喂呜") == ("1005", 100.0)
    assert "喂鸣喂鸣" not in augment_aliases()["1005"]
    assert matcher.match("幽灵条目")[0] is None
    configure({"aliases": {"1005": []}})
    assert augment_aliases()["1005"] == []
    configure({})
    assert augment_aliases()["1005"] == ["喂鸣喂鸣"]


@pytest.mark.parametrize("raw", [None, [], "bad", {"aliases": []}, {"rarity_fix": False},
    {"aliases": {"1": "单个字符串", "x": ["名字"], "2": [None], "3": [""], "4": ["a"*65],
                 "5": ["a\nb"], "6": ["a"]*9, "7": ["a\u200bb"]}},
    {"rarity_fix": {"1": "kGold", "2": None, "3": True, "x": "gold"}}])
def test_bad_rules_ignored_and_logged(raw, caplog):
    with caplog.at_level("INFO"):
        configure(raw)
    assert cloud.get()["augments"] == {"aliases": {}, "rarity_fix": {}}
    assert "云控海克斯" in caplog.text and "忽略" in caplog.text


def test_limits_and_valid_siblings(caplog):
    with caplog.at_level("INFO"):
        configure({"aliases": {str(i): [f"别名{j}" for j in range(8)] for i in range(1, 140)},
                   "rarity_fix": {str(i): "gold" for i in range(1, 140)}})
    cfg = cloud.get()["augments"]
    assert sum(map(len, cfg["aliases"].values())) == 512
    assert len(cfg["rarity_fix"]) == 128
    assert "超过" in caplog.text
    configure({"aliases": {"1": ["  名字  ", "名字"], "2": False}, "rarity_fix": {"1": "gold", "2": "bad"}})
    assert cloud.get()["augments"] == {"aliases": {"1": ["名字"]}, "rarity_fix": {"1": "gold"}}


def test_rarity_overlay_does_not_mutate_snapshot_or_cache(tmp_path):
    store = DataStore(tmp_path)
    raw = {"augments": {"2078": {"id": "2078", "rarity": "prismatic"},
                         "1005": {"id": "1005", "rarity": "gold"}}}
    hero = {"augments": [{"id": "2078", "rarity": "prismatic"}, {"id": "1005", "rarity": "gold"}]}
    original = copy.deepcopy((raw, hero))
    store.official = SimpleNamespace(snapshot=SimpleNamespace(static=raw, heroes={"17": hero}))
    configure({"rarity_fix": {"2078": "silver", "1005": "prismatic"}})
    assert store.augments["2078"]["rarity"] == "silver"
    assert store.augments is store.augments
    assert store.official_hero("17")["augments"][1]["rarity"] == "prismatic"
    assert (raw, hero) == original
    configure({})
    assert store.augments["2078"]["rarity"] == "gold"
    assert store.augments["1005"]["rarity"] == "gold"
    assert store.official_hero("17")["augments"][0]["rarity"] == "gold"


def test_cloud_changes_update_live_detector_and_revoke(tmp_path):
    store = DataStore(tmp_path)
    engine = Engine.__new__(Engine)
    engine.settings, engine.store = Settings(), store
    evaluations = []
    engine._reevaluate = lambda: evaluations.append(True)
    det = AugmentDetector(NameMatcher({}))
    engine._sync_augment_tables(det)
    original_matcher = det.matcher
    engine._sync_augment_tables(det)
    assert det.matcher is original_matcher
    assert len(evaluations) == 1
    configure({"aliases": {"1005": ["热修名字"]}, "rarity_fix": {"1005": "silver"}})
    engine._sync_augment_tables(det)
    assert det.matcher.match("热修名字") == ("1005", 100.0)
    assert det.rarities["1005"] == "silver"
    configure({})
    engine._sync_augment_tables(det)
    assert det.matcher.match("热修名字")[0] is None
    assert det.rarities["1005"] == store.augments["1005"]["rarity"] == "gold"


def test_cached_cloud_is_validated_offline(tmp_path, monkeypatch):
    path = tmp_path / "cloud.json"
    path.write_text(json.dumps({"augments": {"aliases": {"1005": ["离线名字"]},
                                              "rarity_fix": {"1005": "silver"}}}), encoding="utf-8")
    monkeypatch.setattr(cloud, "_path", lambda: path)
    monkeypatch.setattr(cloud, "_cache", None)
    assert augment_aliases()["1005"] == ["离线名字"]
    assert tencent.rarity_fixes()["1005"] == "silver"


def test_open_hero_panel_updates_and_revokes_cloud_rarity(store):
    from test_panel_offline import panel_engine

    engine = panel_engine(store)
    original = engine.hero_panel("17")
    aid = original["augments"][0]["id"]
    before = original["augments"][0]["rarity"]
    changed = "silver" if before != "silver" else "gold"
    configure({"rarity_fix": {aid: changed}})
    updated = engine.hero_panel("17")
    assert updated["augments"][0]["rarity"] == changed
    assert original["augments"][0]["rarity"] == before
    assert engine.hero_panel("17") is updated
    configure({})
    assert engine.hero_panel("17")["augments"][0]["rarity"] == before


@pytest.mark.parametrize("change", ["none", "static", "cloud"])
def test_data_refresh_during_offer_preserves_published_tracking(tmp_path, monkeypatch, change):
    from test_ocr_stability import setup_detector

    det, ocr, clock, rect, strip = setup_detector(monkeypatch)
    store = DataStore(tmp_path)
    store.static = {"augments": {str(i): {"name": n} for i, n in
                    enumerate(["甲甲", "乙乙", "丙丙", "丁丁"])}}
    engine = Engine.__new__(Engine)
    engine.store, engine.settings, engine._detector = store, Settings(), det
    evaluations = []
    engine._reevaluate = lambda: evaluations.append(True)
    engine._sync_augment_tables(det)
    ocr.values = ["甲甲", "乙乙", "丙丙"]
    assert det.check_strip(strip, rect).slots == ["0", "1", "2"]
    stable, published, matcher = det._stable, det._last_result, det.matcher
    pending = det._pending
    revision = engine._augment_tables_revision
    assert revision[0] is store.augments  # Keep the object alive; do not save id().

    def refresh():
        if change == "static":
            store.static = copy.deepcopy(store.static)
            store.static["augments"]["3"]["name"] = "新名字"
        elif change == "cloud":
            configure({"aliases": {"1": ["云控名字"]}})

    store.refresh = refresh
    engine._stop = threading.Event()
    engine._data_wake = SimpleNamespace(clear=lambda: None, wait=lambda _: engine._stop.set())
    engine._warm_up = engine._load_icons = lambda: None
    engine._data_loop()  # Real data loop; one background refresh while the offer is open.
    before_sync = len(evaluations)  # Existing data-loop stats evaluation is independent.
    engine._sync_augment_tables(det)
    assert det._stable is stable and det._last_result is published and det._pending is pending
    if change == "none":
        assert det.matcher is matcher and engine._augment_tables_revision is revision
        assert len(evaluations) == before_sync
        assert det.check_strip(strip, rect).source == "cache"
        assert ocr.calls == 6
    else:
        assert det.matcher is not matcher and det._cells == [None]*3
        assert len(evaluations) == before_sync + 1
        ocr.values = ["甲甲", "乙乙", "丙丙"]
        assert det.check_strip(strip, rect).slots == published.slots  # No intervening empty labels.
