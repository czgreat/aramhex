"""选人结束后的加载空档不能误判秒退；下一次选人和实际离局仍清旧身份。"""

import pytest

from lolhex.config import Settings
from lolhex.engine import Engine
from lolhex.vision.detector import Offer


@pytest.fixture
def observer(store, tmp_path, monkeypatch):
    import lolhex.engine as module

    now = [1000.0]
    scene = {"game": None, "cs": {"mine": "17", "team": [], "bench": []}}
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(Engine, "_prefetch_hero", lambda *args: None)
    monkeypatch.setattr(module.win, "find_game_window", lambda: scene["game"])
    monkeypatch.setattr(module.win, "find_client_window", lambda: 2)
    monkeypatch.setattr(module.win, "is_foreground", lambda hwnd: True)
    engine = Engine(Settings(), home=tmp_path, store=store)
    # 只替换截图识别结果；场景转换、选人处理和结束清理由真实 observer 执行。
    monkeypatch.setattr(engine, "_champ_select_scan", lambda hwnd: engine._apply_champ_select(scene["cs"]))

    def tick(after=0, **changes):
        now[0] += after
        scene.update(changes)
        engine._observe_once()

    tick()
    assert engine.state["hero_id"] == "17"
    return engine, tick


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("gap", [10, 30])
def test_launch_gap_preserves_hero_until_game_then_end_clears(observer, manual, gap):
    engine, tick = observer
    if manual:
        engine.set_hero("17")
    tick(cs=False)
    tick(after=gap, cs=False)
    assert engine.state["hero_id"] == "17"
    assert engine._manual_hero is manual
    tick(game=1)
    assert engine.state["phase"] == "ingame"
    assert engine.state["hero_id"] == "17"
    assert engine._manual_hero is manual
    engine._on_offer(Offer(["2128", "1029", "1373"], [], [], [None] * 3))
    assert engine.state["offer"] and engine.state["evaluation"]
    tick(game=None, cs=False)
    assert engine.state["hero_id"] is None
    assert not engine._manual_hero
    assert engine.state["offer"] is None and engine.state["evaluation"] is None


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("unknown", [None, {"mine": None, "team": [], "bench": []}])
def test_dodge_next_champ_select_clears_before_new_hero(observer, manual, unknown):
    engine, tick = observer
    if manual:
        engine.set_hero("17")
    tick(cs=False)
    tick(after=10, cs=False)
    assert engine.state["hero_id"] == "17"
    tick(cs=unknown)
    assert engine.state["hero_id"] is None
    assert not engine._manual_hero
    tick(cs={"mine": "1", "team": [], "bench": []})
    assert engine.state["hero_id"] == "1"
    assert not engine._manual_hero


@pytest.mark.parametrize("manual", [False, True])
def test_short_or_unreadable_champ_select_keeps_identity(observer, manual):
    engine, tick = observer
    if manual:
        engine.set_hero("17")
    tick(cs=False)
    tick(after=6, cs=None)
    assert engine.state["hero_id"] == "17"
    assert engine._manual_hero is manual
    tick(after=1, cs={"mine": None, "team": [], "bench": []})
    assert engine.state["hero_id"] == "17"
    assert engine._manual_hero is manual


def test_long_background_gap_clears_at_next_champ_select(observer):
    engine, tick = observer
    tick(after=91, cs={"mine": None, "team": [], "bench": []})
    assert engine.state["hero_id"] is None
    tick(cs={"mine": "1", "team": [], "bench": []})
    assert engine.state["hero_id"] == "1"
