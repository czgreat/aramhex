"""A reroll must never expose new cards without scores to the overlay timer."""
import threading

import pytest

from lolhex.config import Settings
from lolhex.engine import Engine
from lolhex.vision.detector import Offer
from lolhex.vision.window import Rect


def offer(slots):
    return Offer(slots, [], [], [(440, 420, 750, 470), (810, 420, 1120, 470),
                                 (1180, 420, 1490, 470)])


@pytest.mark.parametrize("column", [0, 1, 2])
def test_overlay_stays_visible_while_reroll_is_evaluated(store, tmp_path, monkeypatch, column):
    from PySide6.QtWidgets import QApplication
    from lolhex.ui import overlay
    import lolhex.engine as module

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(overlay.win, "IS_WINDOWS", False)
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase="ingame", hero_id="17")
    initial = offer(["2128", "1029", "1373"])
    engine._on_offer(initial)
    previous = engine.snapshot()
    layer = overlay.BadgeLayer()
    rect = Rect(0, 0, 1920, 1080)
    layer.update_from(previous, rect)
    assert layer.isVisible()
    slots = list(initial.slots)
    slots[column] = "2096"
    started, release = threading.Event(), threading.Event()
    evaluate = module.recommend.evaluate
    errors = []

    def slow_evaluation(*args):
        started.set()
        if not release.wait(5):
            raise TimeoutError("test did not release scoring")
        return evaluate(*args)

    def publish():
        try:
            engine._on_offer(offer(slots))
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(module.recommend, "evaluate", slow_evaluation)
    worker = threading.Thread(target=publish)
    worker.start()
    try:
        assert started.wait(5)
        during = engine.snapshot()
        layer.update_from(during, rect)  # The real UI timer can run while scoring waits.
        assert layer.isVisible(), "one reroll hid all three badges"
        assert during["offer"]["slots"] == previous["offer"]["slots"]
        assert during["offer"]["pending_columns"] == tuple(i == column for i in range(3))
        drawn = {r["index"] for r, *_ in layer._badge_layout(during)}
        assert drawn == {0, 1, 2} - {column}, "the changed column still draws its old score"
        assert during["evaluation"] == previous["evaluation"]
    finally:
        release.set()
        worker.join(5)
        layer.close()
        app.processEvents()
    assert not worker.is_alive() and not errors
    after = engine.snapshot()
    assert after["offer"]["slots"] == slots
    assert [s["augment_id"] for s in after["evaluation"]["slots"]] == slots


@pytest.mark.parametrize("change", ["close", "end", "hero", "newer"])
def test_pending_reroll_cannot_overwrite_newer_state(store, tmp_path, monkeypatch, change):
    import lolhex.engine as module

    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase="ingame", hero_id="17")
    initial = offer(["2128", "1029", "1373"])
    engine._on_offer(initial)
    original = module.recommend.evaluate
    active = False
    expected = []

    def change_while_scoring(*args):
        nonlocal active
        result = original(*args)
        if not active:
            active = True
            if change == "close":
                engine._on_offer(None)
            elif change == "end":
                engine._on_game_end()
            elif change == "hero":
                engine._set(hero_id="1")
                engine._reevaluate()
            else:
                engine._on_offer(offer(["1373", "1029", "2128"]))
            expected.append(engine.snapshot())
        return result

    monkeypatch.setattr(module.recommend, "evaluate", change_while_scoring)
    engine._on_offer(offer(["2128", "2096", "1373"]))
    assert engine.snapshot() == expected[0]


def test_first_offer_closed_during_scoring_does_not_reappear(store, tmp_path, monkeypatch):
    import lolhex.engine as module

    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase="ingame", hero_id="17")
    original = module.recommend.evaluate

    def close_while_scoring(*args):
        result = original(*args)
        engine._on_offer(None)
        return result

    monkeypatch.setattr(module.recommend, "evaluate", close_while_scoring)
    engine._on_offer(offer(["2128", "1029", "1373"]))
    assert engine.state["offer"] is None and engine.state["evaluation"] is None
