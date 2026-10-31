"""Release 1.1.4 integration checks for group timeouts and publication."""
from dataclasses import replace

from lolhex.config import Settings
from lolhex.engine import Engine
from lolhex.vision.detector import Offer
from test_offer_group_transition import group_detector
from test_unread_reading_label import BOXES, labels


def test_group_timeout_does_not_publish_an_old_single_read(monkeypatch):
    det, _, clock, _, _ = group_detector(monkeypatch)
    first = replace(det._stable, slots=["3", "4", "5"])
    assert det._stabilize(first, [True] * 3).group_changed
    # The middle name was seen only during the animation. All later reads fail.
    current = replace(first, slots=["3", None, "5"])
    clock[0] += .3
    assert det._stabilize(current, [True] * 3).group_changed
    clock[0] += 2 * det.pending_seconds + .01
    result = det._stabilize(current, [True] * 3)
    assert result.slots == ["3", None, "5"]
    assert not result.group_changed


def test_group_timeout_can_use_a_name_first_seen_in_the_current_frame(monkeypatch):
    det, _, clock, _, _ = group_detector(monkeypatch)
    first = replace(det._stable, slots=["3", None, "5"])
    assert det._stabilize(first, [True] * 3).group_changed
    clock[0] += 2 * det.pending_seconds + .01
    assert det._stabilize(replace(first, slots=["3", "4", "5"]), [True] * 3).slots == ["3", "4", "5"]


def test_group_wait_invalidates_first_offer_still_being_scored(store, tmp_path, monkeypatch):
    import lolhex.engine as module

    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase="ingame", hero_id="17")
    original = module.recommend.evaluate

    def group_changes_while_scoring(*args):
        result = original(*args)
        engine._on_offer(Offer([None] * 3, [], [], [], group_changed=True))
        return result

    monkeypatch.setattr(module.recommend, "evaluate", group_changes_while_scoring)
    engine._on_offer(Offer(["2128", "1029", "1373"], [], [], []))
    assert engine.state["offer"] is None
    assert engine.state["evaluation"] is None


def test_reading_expiry_repaints_without_a_new_offer(store, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from lolhex.ui import overlay

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(overlay.win, "IS_WINDOWS", False)
    clock = [100.0]
    monkeypatch.setattr(overlay.time, "monotonic", lambda: clock[0])
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase="ingame", hero_id="17")
    layer = overlay.BadgeLayer()
    rect = overlay.win.Rect(0, 0, 1920, 1080)
    updates = []
    monkeypatch.setattr(layer, "update", lambda *args: updates.append(args))
    try:
        engine._on_offer(Offer(["2128", None, "1373"], [], [], BOXES))
        snap = engine.snapshot()
        layer.update_from(snap, rect)
        assert labels(layer, snap)[1] == "识别中…"
        updates.clear()
        clock[0] += 2.1
        layer.update_from(snap, rect)  # Same snapshot, as in Overlay's 250 ms timer.
        assert labels(layer, snap)[1] == "未知"
        assert len(updates) == 1 and len(updates[0]) == 1
        dirty = updates[0][0]
        assert not dirty.isEmpty()
        for index in (0, 2):
            assert not dirty.intersects(layer._painted[1][index][1])
        # Recognition clears the timer; losing the name later starts a new one.
        engine._on_offer(Offer(["2128", "1029", "1373"], [], [], BOXES))
        clock[0] += 1
        engine._on_offer(Offer(["2128", None, "1373"], [], [], BOXES))
        assert engine.snapshot()["offer"]["unread_since"][1] == clock[0]
        engine._on_offer(None)
        clock[0] += 60
        engine._on_offer(Offer(["2128", None, "1373"], [], [], BOXES))
        assert engine.snapshot()["offer"]["unread_since"][1] == clock[0]
    finally:
        layer.close()
        app.processEvents()
