"""界面读面板不能跟着慢网/断网等待腾讯数据。"""
import threading
from types import SimpleNamespace

from conftest import fixture
from lolhex.data import tencent
from lolhex.engine import Engine
from lolhex import recommend


def panel_engine(store):
    engine = Engine.__new__(Engine)
    engine.store = store
    engine.settings = SimpleNamespace(smoothing_games=300)
    engine._panel_cache = (None, None)
    engine._lock = threading.RLock()
    engine._evaluation_revision = 0
    engine.state = {"offer": None, "hero_id": "17"}
    return engine


def test_cold_panel_uses_bundled_data_without_network(store, monkeypatch):
    calls = []
    monkeypatch.setattr(tencent, "fetch_hero_hex", lambda hid: calls.append(hid) or {})
    (store.root / "hero" / "17.json").unlink()
    store._heroes.clear()
    panel = panel_engine(store).hero_panel("17")
    assert calls == []
    assert panel["hero"]["id"] == "17"
    assert panel["builds"]["core"]
    assert panel["augments"]


def test_recommendation_on_expired_data_does_not_retry_network(store, monkeypatch):
    cached = store.official_hero("17", allow_network=False)
    cached["fetched_at"] = 0
    calls = []
    monkeypatch.setattr(tencent, "fetch_hero_hex", lambda hid: calls.append(hid) or {})
    result = recommend.evaluate(store, "17", ["2128", "1029", "1373"])
    assert calls == []
    assert result["best_index"] == 1


def test_panel_does_not_wait_for_background_fetch_and_refreshes_after_it(store, monkeypatch):
    started, finish = threading.Event(), threading.Event()
    calls = []

    def slow_fetch(hid):
        calls.append(threading.current_thread().name)
        started.set()
        assert finish.wait(5), "test did not release simulated network request"
        return fixture("tencent_hero17.json")

    monkeypatch.setattr(tencent, "fetch_hero_hex", slow_fetch)
    monkeypatch.setattr(tencent, "fetch_hero_file", lambda hid: fixture("tencent_hero17_spells.json"))
    cached = store.official_hero("17", allow_network=False)
    cached["fetched_at"] = 0
    engine = panel_engine(store)
    reevaluated = []
    monkeypatch.setattr(engine, "_reevaluate", lambda: reevaluated.append(True))
    worker = threading.Thread(target=engine._prefetch_hero, args=("17",), name="test-prefetch")
    worker.start()
    try:
        assert started.wait(2)
        panel = engine.hero_panel("17")
        assert panel["augments"]
        assert calls == ["test-prefetch"]
        assert worker.is_alive()
        assert engine._panel_cache[1] is panel
    finally:
        finish.set()
        worker.join(5)
    assert not worker.is_alive()
    assert engine._panel_cache == (None, None)
    assert reevaluated == [True]
    assert engine.hero_panel("17")["augments"]
    assert calls == ["test-prefetch"]
