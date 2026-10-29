"""Recognition tuning preserves default timing and applies/revokes cloud rules live."""
from dataclasses import replace
from types import SimpleNamespace
import threading

import pytest

from lolhex import cloudsettings
from lolhex.config import Settings
from lolhex.engine import Engine
from lolhex.vision import window
from test_ocr_stability import setup_detector
from test_reroll_continuity import change_columns


FIELDS = {"recheck_seconds": (5.0, 1, 60), "pending_seconds": (.9, .1, 5),
          "low_match_reads": (3, 1, 10), "active_hold_s": (5.0, 0, 30)}


def test_default_recheck_remains_five_seconds(monkeypatch):
    det, ocr, clock, rect, strip = setup_detector(monkeypatch)
    assert det.recheck_seconds == Settings().recheck_seconds == 5
    clock[0] = 104.99
    assert det.check_strip(strip, rect).source == "cache" and ocr.calls == 3
    ocr.values = ["甲甲"]
    clock[0] = 105
    assert det.check_strip(strip, rect).source == "strip" and ocr.calls == 4


def test_default_pending_hides_at_point_nine_seconds(monkeypatch):
    det, _, clock, _, _ = setup_detector(monkeypatch)
    assert det.pending_seconds == Settings().pending_seconds == .9
    original = det._stable
    def observe(aid):
        return det._stabilize(replace(original, slots=[aid, "1", "2"]), [True, False, False])
    assert observe("3").slots[0] == "0"
    clock[0] += .89
    assert observe("2").slots[0] == "0"
    clock[0] += .02
    assert observe("3").slots[0] is None


def test_default_low_match_state_clears_after_three_real_reads(monkeypatch):
    det, ocr, clock, rect, strip = setup_detector(monkeypatch)
    assert det.low_match_reads == Settings().low_match_reads == 3
    stable = det._stable
    change_columns(det, rect, strip, 0, 1, 2)
    ocr.values = [""] * 9
    for count in range(1, 4):
        clock[0] += .3
        assert det.check_strip(strip, rect) is None
        assert det._low_match_reads == count and ocr.calls == 3 + count*3
        assert (det._stable is stable) if count < 3 else (det._stable is None)


def test_default_and_custom_active_hold_boundaries():
    engine = Engine.__new__(Engine)
    engine.settings = Settings(idle_scan_ms=1000)
    assert engine.settings.active_hold_s == 5
    assert engine._strip_step(False, 5) == .3
    assert engine._strip_step(False, 5.01) == 1
    engine.settings.active_hold_s = 0
    assert engine._strip_step(False, .01) == 1
    assert engine._strip_step(True, 99) == .3


def test_custom_detector_thresholds_change_real_behavior(monkeypatch):
    det, ocr, clock, rect, strip = setup_detector(monkeypatch)
    det.recheck_seconds = 12
    clock[0] = 111.99
    assert det.check_strip(strip, rect).source == "cache" and ocr.calls == 3
    clock[0] = 112
    ocr.values = ["甲甲"]
    assert det.check_strip(strip, rect).source == "strip" and ocr.calls == 4
    det.pending_seconds = .2
    original = det._stable
    det._stabilize(replace(original, slots=["3", "1", "2"]), [True, False, False])
    clock[0] += .21
    assert det._stabilize(replace(original, slots=["2", "1", "2"]), [True, False, False]).slots[0] is None
    det.low_match_reads = 1
    change_columns(det, rect, strip, 0, 1, 2)
    ocr.values = [""] * 3
    assert det.check_strip(strip, rect) is None
    assert det._stable is None and det._low_match_reads == 1


@pytest.mark.parametrize("field", FIELDS)
def test_cloud_limits_and_invalid_values(field):
    default, lo, hi = FIELDS[field]
    validate = cloudsettings.SPEC[field]
    assert getattr(Settings(), field) == default
    assert validate(lo) == lo and validate(hi) == hi
    bad = [lo-.01, hi+.01, True, False, None, "3", float("nan"), float("inf")]
    if field == "low_match_reads": bad += [3.5, 3.0]
    for value in bad:
        assert validate(value) is None


def test_force_does_not_persist_and_offline_defaults_roundtrip(tmp_path):
    settings = Settings()
    custom = {"recheck_seconds": 12., "pending_seconds": 1.5,
              "low_match_reads": 6, "active_hold_s": 10.}
    cloudsettings.apply(settings, {"settings": [{"id": "timing", "force": custom}]}, "1.1.3")
    path = tmp_path / "config.json"
    settings.save(path)
    loaded = Settings.load(path)
    assert all(getattr(loaded, k) == values[0] for k, values in FIELDS.items())
    cloudsettings.apply(settings, {}, "1.1.3")
    assert all(getattr(settings, k) == values[0] for k, values in FIELDS.items())
    cloudsettings.apply(settings, {"settings": [{"defaults": custom}]}, "1.1.3")
    settings.save(path)
    loaded = Settings.load(path)
    assert all(getattr(loaded, k) == v for k, v in custom.items())


def test_real_vision_loop_applies_and_revokes_parameters_between_scans(monkeypatch):
    import lolhex.engine as module
    clock, observed = [100.0], []
    settings = Settings()
    custom = {"recheck_seconds": 12., "pending_seconds": 1.5,
              "low_match_reads": 6, "active_hold_s": 10.}
    stable = object()
    det = SimpleNamespace(ensure_calibration=lambda *a: None, calibrated_for=lambda *a: True,
                          confirmed_for=lambda *a: True, _stable=stable)
    class Stop:
        def is_set(self): return len(observed) >= 3
        def wait(self, seconds): clock[0] += seconds
    def scan(det, rect, *, full, **kwargs):
        if full: return None
        observed.append((det.recheck_seconds, det.pending_seconds, det.low_match_reads))
        cfg = {"settings": [{"id": "timing", "force": custom}]} if len(observed) == 1 else {}
        cloudsettings.apply(settings, cfg, "1.1.3")
        assert det._stable is stable
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(window, "lower_thread_priority", lambda: None)
    monkeypatch.setattr(window, "find_game_window", lambda: 1)
    monkeypatch.setattr(window, "client_rect", lambda _: window.Rect(0, 0, 1920, 1080))
    monkeypatch.setattr(window, "is_foreground", lambda _: True)
    engine = Engine.__new__(Engine)
    engine._stop, engine._rescan, engine._detector = Stop(), threading.Event(), det
    engine.settings, engine.state = settings, {"phase": "ingame", "vision": {}}
    engine._observed_scan = scan
    engine._sync_augment_tables = lambda det: None  # 本测试只控制识别参数（#46 起每轮会同步名字表）
    engine._vision_loop()
    assert observed == [(5., .9, 3), (12., 1.5, 6), (5., .9, 3)]
