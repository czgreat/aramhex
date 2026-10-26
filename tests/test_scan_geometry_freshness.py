"""在途 OCR 不能把旧窗口坐标发布到新窗口；切后台本身不触发重新校准。"""
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from lolhex.config import Settings
from lolhex.engine import Engine
from lolhex.vision import capture, window
from lolhex.vision.detector import Offer


def setup_scan(monkeypatch, store):
    engine = Engine.__new__(Engine)
    engine._lock = threading.RLock()
    engine._scan_epoch = 0
    engine._evaluation_revision = 0
    engine._last_eval_log = None
    engine._manual_hero = False
    engine._data_wake = threading.Event()
    engine._rescan = threading.Event()
    engine.store = store
    engine.settings = Settings()
    engine.state = {"phase": "ingame", "hero_id": "17", "offer": None,
                    "evaluation": None, "vision": {}}
    current = {"hwnd": 7, "rect": window.Rect(0, 0, 1920, 1080), "foreground": True}
    monkeypatch.setattr(window, "find_game_window", lambda: current["hwnd"])
    monkeypatch.setattr(window, "client_rect", lambda hwnd: current["rect"])
    monkeypatch.setattr(window, "is_foreground", lambda hwnd: current["foreground"])
    captures, cleared = [], []
    monkeypatch.setattr(capture, "grab", lambda rect: captures.append(rect) or np.zeros((48, 448, 3), np.uint8))
    offer = Offer(["2128", "1029", "1373"], ["a", "b", "c"], [100] * 3,
                  [(100, 400, 300, 440), (700, 400, 900, 440), (1300, 400, 1500, 440)])
    detector = SimpleNamespace(calibration=object(), clear_tracking=lambda: cleared.append(True),
        scan_full=lambda *a: offer, check_strip=lambda *a: offer,
        strip_rect=lambda rect: rect, confirmed_for=lambda *a: True)
    engine._detector = detector
    return engine, detector, current, captures, cleared


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("change", ["move", "resize", "window", "session", "rescan"])
def test_geometry_or_session_changes_during_scan_drop_result(monkeypatch, store, full, change):
    engine, detector, current, captures, cleared = setup_scan(monkeypatch, store)
    old_rect = current["rect"]
    original = detector.scan_full

    def scan(*args):
        result = original(*args)
        if change == "move":
            current["rect"] = window.Rect(300, 200, 1920, 1080)
        elif change == "resize":
            current["rect"] = window.Rect(0, 0, 2560, 1440)
        elif change == "window":
            current["hwnd"] = 8
        elif change == "rescan":
            engine.rescan()
        else:
            engine._clear_selection()
            engine._set(phase="ingame", hero_id="17")  # ABA: same phase/hero, different game.
        return result

    detector.scan_full = detector.check_strip = scan
    result = engine._observed_scan(detector, old_rect, full=full, reason="test", hwnd=7)
    assert result is None and engine._scan_discarded
    assert len(captures) == 1 and len(cleared) == 1
    assert detector.calibration is None
    assert engine.state["offer"] is None and engine.state["evaluation"] is None


def test_window_move_between_scan_and_publication_is_rejected(monkeypatch, store):
    engine, detector, current, captures, cleared = setup_scan(monkeypatch, store)
    result = engine._observed_scan(detector, current["rect"], full=True, reason="test", hwnd=7)
    current["rect"] = window.Rect(100, 100, 1920, 1080)
    engine._on_offer(result)
    assert engine.state["offer"] is None and engine.state["evaluation"] is None
    assert len(captures) == 1 and cleared == [True]


def test_window_move_during_recommendation_cannot_publish_old_score(monkeypatch, store):
    import lolhex.engine as module
    engine, detector, current, captures, cleared = setup_scan(monkeypatch, store)
    result = engine._observed_scan(detector, current["rect"], full=True, reason="test", hwnd=7)
    original = module.recommend.evaluate

    def move_during_evaluation(*args):
        score = original(*args)
        current["rect"] = window.Rect(100, 100, 1920, 1080)
        return score

    monkeypatch.setattr(module.recommend, "evaluate", move_during_evaluation)
    engine._on_offer(result)
    assert engine.state["offer"] is None and engine.state["evaluation"] is None
    assert len(captures) == 1 and cleared == [True]


@pytest.mark.parametrize("minimized", [False, True])
def test_background_without_known_geometry_change_preserves_tracking(monkeypatch, store, minimized):
    engine, detector, current, captures, cleared = setup_scan(monkeypatch, store)
    old_rect = current["rect"]
    calibration = detector.calibration
    original = detector.scan_full

    def background(*args):
        current["foreground"] = False
        if minimized:
            current["rect"] = None
        return original(*args)

    detector.scan_full = background
    result = engine._observed_scan(detector, old_rect, full=True, reason="test", hwnd=7)
    engine._on_offer(result)
    assert engine.state["offer"]["slots"] == result.slots
    assert engine.state["evaluation"]
    assert detector.calibration is calibration and cleared == []
    assert len(captures) == 1


def test_changed_window_before_capture_does_no_ocr_or_capture(monkeypatch, store):
    engine, detector, current, captures, cleared = setup_scan(monkeypatch, store)
    current["hwnd"] = 8
    detector.scan_full = lambda *a: pytest.fail("obsolete window must not reach OCR")
    assert engine._observed_scan(detector, current["rect"], full=True, reason="test", hwnd=7) is None
    assert captures == [] and cleared == [True]


def test_new_cards_do_not_expose_previous_evaluation_while_recomputing(monkeypatch, store):
    import lolhex.engine as module
    engine, detector, current, captures, cleared = setup_scan(monkeypatch, store)
    engine.state.update(offer={"slots": ["old", "old", "old"]}, evaluation={"advice": "old score"})
    result = engine._observed_scan(detector, current["rect"], full=True, reason="test", hwnd=7)
    original = module.recommend.evaluate
    observed = []

    def inspect_during_evaluation(*args):
        with engine._lock:
            observed.append(engine.state["evaluation"])
        return original(*args)

    monkeypatch.setattr(module.recommend, "evaluate", inspect_during_evaluation)
    engine._on_offer(result)
    assert observed == [None]
    assert engine.state["evaluation"]["best_index"] == 1


@pytest.mark.parametrize("move", [False, True])
def test_calibration_file_only_changes_after_window_validation(monkeypatch, store, tmp_path, move):
    from lolhex.vision.detector import AugmentDetector, Calibration
    from lolhex.vision.matcher import NameMatcher
    engine, mock, current, captures, cleared = setup_scan(monkeypatch, store)
    path = tmp_path / "calibration.json"
    detector = AugmentDetector(NameMatcher({}), calib_path=path)
    path.write_bytes(b"previous calibration evidence")
    original = mock.scan_full
    old_rect = current["rect"]

    def calibrate(*args):
        detector.calibration = Calibration.default(1920, 1080)
        detector._save_calibration()
        if move:
            current["rect"] = window.Rect(100, 100, 1920, 1080)
        return original(*args)

    detector.scan_full = calibrate
    engine._detector = detector
    result = engine._observed_scan(detector, old_rect, full=True, reason="test", hwnd=7)
    if move:
        assert result is None
        assert path.read_bytes() == b"previous calibration evidence"
    else:
        assert result
        assert b'"boxes"' in path.read_bytes()
