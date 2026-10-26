"""玩家模式禁止官方资料/图标回源，云控仍能缩短长间隔。"""
import json

import pytest

from lolhex import cloud
from lolhex.config import Settings
from lolhex.data.store import DataStore
from lolhex.engine import Engine


@pytest.mark.parametrize("minutes,expected", [(0, 0), (60, 60), (43200, 43200),
                                               (999999, 43200), (-1, 0), (True, 0),
                                               ("bad", 0), (float("nan"), 0)])
def test_official_cloud_interval_supports_disabled_hour_and_month(monkeypatch, minutes, expected):
    monkeypatch.setattr(cloud, "_cache", cloud._merge({"update": {"official_minutes": minutes}}))
    assert cloud.official_minutes() == expected


def test_official_cloud_sources_are_separate_from_statistics(monkeypatch):
    monkeypatch.setattr(cloud, "_cache", cloud._merge({
        "mirrors": {"official": ["https://our.example/u/official", "http://other.example/x"],
                    "data": ["https://our.example/u/data"]},
        "update": {"official_minutes": 43200, "data_minutes": 60}}))
    assert cloud.mirrors("official") == ["https://our.example/u/official"]
    assert cloud.data_minutes() == 60


def test_player_ignores_old_individual_cache_and_never_contacts_tencent(tmp_path, monkeypatch):
    from lolhex.data import tencent
    def fail(*a, **kw):
        pytest.fail("Player mode must not request Tencent")
    for name in ("fetch_hero_list", "fetch_kiwi_augments", "fetch_items", "fetch_summoner_spells",
                 "fetch_versions", "fetch_hero_file", "fetch_hero_hex"):
        monkeypatch.setattr(tencent, name, fail)
    monkeypatch.setattr(DataStore, "_refresh_static", fail)
    (tmp_path / "static.json").write_text(json.dumps({"patch": "old", "heroes": {"17": {}},
                                                       "augments": {"1": {}}}), encoding="utf-8")
    s = DataStore(tmp_path, managed=True)
    s.refresh(force=True)
    assert len(s.heroes) >= 173
    assert s.static["patch"] != "old"
    assert s.official_hero("200")["builds"]
    assert s.official_hero("234", allow_network=True)["builds"]
    assert not (tmp_path / "hero").exists()


def test_engine_uses_managed_data_and_local_icons(tmp_path, monkeypatch):
    from lolhex.data.icons import IconCache
    from lolhex.vision import portraits
    monkeypatch.setattr(Engine, "_apply_rec_model", lambda self: None)
    def fail(*a, **kw):
        pytest.fail("Player portraits and item icons must not download")
    monkeypatch.setattr(portraits, "ensure_icons", fail)
    monkeypatch.setattr(IconCache, "_fetch", fail)
    e = Engine(Settings(), home=tmp_path)
    assert e.store.official is not None
    e._load_icons()
    assert e.icons.local("item", "1001", "https://game.gtimg.cn/missing.png")
    assert e.icons.local("item", "unknown", "https://game.gtimg.cn/missing.png") is None
    assert not e._data_wake.is_set()
    e.cloud_changed()
    assert e._data_wake.is_set()
    assert not e.store.hold()
    e.state.update(phase="client", champ_select={"cards": ["17"]})
    assert e.store.hold()
    e.state.update(champ_select=None)
    assert not e.store.hold()  # 停留在大厅不能永久挡住资料激活。
