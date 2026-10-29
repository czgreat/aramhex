"""1.1.2：第一次运行连不上腾讯 CDN 时，用内置的官方资料；有缓存用缓存，联网成功换成最新的（玩家反馈 10-23）。"""

import json

from lolhex.data import store as store_mod, tencent
from lolhex.data.store import BUNDLED_STATIC, DataStore, bundled_static


def _offline(monkeypatch):
    def fail(*a, **k):
        raise OSError("SSLEOFError: EOF occurred in violation of protocol")
    monkeypatch.setattr(tencent, "fetch_hero_list", fail)


def test_bundled_file_is_complete():
    d = json.loads(BUNDLED_STATIC.read_text(encoding="utf-8"))
    assert len(d["heroes"]) >= 150 and len(d["augments"]) >= 200
    assert d["items"] and d["spells"] and d["patch"]
    assert "fetched_at" not in d  # 生成时不写抓取时间，读的时候记 0
    # 认英雄要称号和名字，认海克斯要名字和稀有度
    assert all(h.get("name") and h.get("nick") and h.get("alias") for h in d["heroes"].values())
    assert d["augments"]["3170"]["name"] and d["augments"]["3170"]["rarity"] == "gold"


def test_no_cache_and_offline_uses_bundled(tmp_path, monkeypatch):
    _offline(monkeypatch)
    s = DataStore(tmp_path)
    assert s.static.get("bundled") and s._stale(s.static)  # 照样算过期：启动时先联网
    s.refresh()
    assert s.last_error and s.static.get("bundled")  # 连不上：继续用内置的，界面照常提示官方资料获取失败
    assert len(s.heroes) >= 150 and s.mayhem_augments()
    assert s.resolve_hero("暴走萝莉") == "222"  # 日志里读到的称号能对上英雄（金克丝）
    assert not (tmp_path / "static.json").exists()  # 内置的不写成缓存


def test_cache_preferred_over_bundled(tmp_path):
    cache = {"fetched_at": 1, "patch": "16.20", "heroes": {"1": {"name": "安妮", "title": "黑暗之女"}},
             "augments": {"1001": {"name": "x"}}}
    (tmp_path / "static.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    s = DataStore(tmp_path)
    assert not s.static.get("bundled") and s.static["patch"] == "16.20"


def test_broken_or_empty_cache_falls_back(tmp_path):
    for text in ("{坏了", json.dumps({"fetched_at": 1, "heroes": {}, "augments": {}})):
        (tmp_path / "static.json").write_text(text, encoding="utf-8")
        assert DataStore(tmp_path).static.get("bundled")


def test_missing_bundled_file(tmp_path):
    assert bundled_static(tmp_path / "nope.json") == {}


def test_offline_hero_uses_bundled_builds(tmp_path, monkeypatch):
    def fail(*a, **k):
        raise OSError("SSLEOFError")
    monkeypatch.setattr(tencent, "fetch_hero_hex", fail)
    s = DataStore(tmp_path)
    d = s.official_hero("222")
    assert d and d.get("bundled") and d.get("fetched_at") == 0
    assert d.get("builds") and d.get("spells")  # 出装、加点都有
    assert not (tmp_path / "hero" / "222.json").exists()  # 内置的不写成缓存
    assert s.official_hero("99999") is None


def test_bundled_icons_unpack_and_stay_stale(tmp_path, monkeypatch):
    from lolhex.data import icons as icons_mod
    from lolhex.data.icons import MAX_AGE_S, IconCache

    fetched = []
    monkeypatch.setattr(IconCache, "_fetch", lambda self, path, url: fetched.append(url))
    c = IconCache(tmp_path)
    p = c.local("item", "1001", "https://example.invalid/1001.png")  # 鞋子
    assert p and open(p, "rb").read(4) == b"\x89PNG"
    import os
    import time
    assert time.time() - os.path.getmtime(p) > MAX_AGE_S  # 记成过期
    assert fetched  # 能联网时照样去下最新的
    assert c.local("spell", "4", None)  # 闪现
    assert c.local("item", "nope", None) is None
    assert icons_mod.bundled_icons(tmp_path / "nope.json") == {}


def test_online_refresh_replaces_bundled(tmp_path, monkeypatch):
    s = DataStore(tmp_path)
    assert s.static.get("bundled")
    monkeypatch.setattr(tencent, "fetch_hero_list", lambda: {})
    monkeypatch.setattr(tencent, "parse_hero_list", lambda raw: {"1": {"name": "安妮", "title": "黑暗之女"}})
    monkeypatch.setattr(tencent, "fetch_kiwi_augments", lambda: [])
    monkeypatch.setattr(tencent, "parse_kiwi_augments", lambda raw: {"1001": {"name": "x"}})
    monkeypatch.setattr(tencent, "fetch_items", lambda: {})
    monkeypatch.setattr(tencent, "parse_items", lambda raw: {})
    monkeypatch.setattr(tencent, "fetch_summoner_spells", lambda: {})
    monkeypatch.setattr(tencent, "parse_summoner_spells", lambda raw: {})
    monkeypatch.setattr(tencent, "fetch_versions", lambda: [{"name": "16.22"}])
    monkeypatch.setattr(store_mod.DataStore, "_merge_cdragon", staticmethod(lambda augments: None))
    s.refresh()
    assert not s.static.get("bundled") and s.static["patch"] == "16.22"
    assert (tmp_path / "static.json").exists()


def test_chain_reaction_is_gold():
    """2078 连锁反应：腾讯标棱彩，游戏里是金色（Gitea #34）。内置资料和解析腾讯原始数据都按金色。"""
    from lolhex.data.store import BUNDLED_HEROES
    assert json.loads(BUNDLED_STATIC.read_text(encoding="utf-8"))["augments"]["2078"]["rarity"] == "gold"
    rows = [a for h in json.loads(BUNDLED_HEROES.read_text(encoding="utf-8"))["heroes"].values()
            for a in h.get("augments") or [] if a["id"] == "2078"]
    assert rows and all(a["rarity"] == "gold" for a in rows)
    raw = [{"augmentID": 2078, "name_cn": "连锁反应", "level": "kPrismatic"},
           {"augmentID": 1005, "name_cn": "喂呜喂呜", "level": "kGold"}]
    got = tencent.parse_kiwi_augments(raw)
    assert got["2078"]["rarity"] == "gold" and got["1005"]["rarity"] == "gold"
