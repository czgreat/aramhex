"""Diagnostic evidence must describe real calls without changing recognition decisions."""
import logging
import threading
from types import SimpleNamespace

import numpy as np
import pytest


@pytest.mark.parametrize("kind", ["detect", "recognize"])
def test_slow_ocr_profiles_lock_and_stages_without_text(monkeypatch, caplog, kind):
    from lolhex.vision.ocr import OCR
    import lolhex.vision.ocr as module
    ticks = iter([100, 100.5, 103, 110, 110.1, 112])
    monkeypatch.setattr(module.time, "perf_counter", lambda: next(ticks))
    result = SimpleNamespace(boxes=None, txts=("私人文字",), scores=(.9,), elapse_list=[.7, 0, 1.5])
    ocr = OCR.__new__(OCR)
    ocr._lock = threading.Lock()
    ocr._engine = lambda *args, **kwargs: result
    with caplog.at_level(logging.INFO):
        getattr(ocr, kind)(np.zeros((48, 448, 3), np.uint8))
        getattr(ocr, kind)(np.zeros((48, 448, 3), np.uint8))
    messages = [r.getMessage() for r in caplog.records if "耗时" in r.getMessage()]
    assert len(messages) == 1  # Repeat slow calls are rate limited.
    assert "等待锁=500毫秒" in messages[0]
    assert "检测=700毫秒" in messages[0] and "认字=1500毫秒" in messages[0]
    assert "私人文字" not in caplog.text


def test_scan_trace_preserves_result_and_measures_capture_to_publication(store, tmp_path, monkeypatch, caplog):
    from lolhex.engine import Engine
    from lolhex.config import Settings
    from lolhex.vision import capture
    from lolhex.vision.detector import Offer
    from lolhex.vision.window import Rect
    import lolhex.engine as module
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine.state.update(phase="ingame", hero_id="17")
    result = Offer(["2128", "1029", "1373"], ["private"]*3, [100]*3, [None]*3)
    detector = SimpleNamespace(scan_full=lambda *args: result, confirmed_for=lambda *args: True)
    monkeypatch.setattr(capture, "grab", lambda *args: np.zeros((48, 448, 3), np.uint8))
    ticks = iter([100, 100.2, 115.2, 116])
    monkeypatch.setattr(module.time, "perf_counter", lambda: next(ticks))
    with caplog.at_level(logging.INFO):
        traced = engine._observed_scan(detector, Rect(0, 0, 1920, 1080), full=True, reason="fallback")
        engine._on_offer(traced)
        engine._on_offer(traced)  # Same slots must not produce a publication log every tick.
    assert traced.slots == result.slots and result.trace is None
    assert engine.state["offer"]["slots"] == result.slots
    assert "trace" not in engine.state["offer"]
    assert "capture=200ms work=15000ms after=800ms total=16000ms frame_age=15800ms" in caplog.text
    assert "reason=fallback" in caplog.text and "私人" not in caplog.text and "private" not in caplog.text
    assert sum("推荐状态发布" in r.getMessage() for r in caplog.records) == 1


@pytest.mark.parametrize("blank,fail", [(True, False), (False, True)])
def test_diagnostics_preserve_blank_and_failure_behavior(monkeypatch, caplog, blank, fail):
    from lolhex.engine import Engine
    from lolhex.vision import capture
    from lolhex.vision.window import Rect
    import lolhex.engine as module
    engine = Engine.__new__(Engine)
    engine.state = {"vision": {}}
    calls = []
    def scan(*args):
        calls.append(1)
        if fail:
            raise RuntimeError("model failed")
    detector = SimpleNamespace(scan_full=scan, confirmed_for=lambda *args: False)
    monkeypatch.setattr(capture, "grab", lambda *args: np.zeros((48, 448, 3), np.uint8))
    monkeypatch.setattr(capture, "is_blank", lambda _: blank)
    ticks = iter([100, 100.1, 102])
    monkeypatch.setattr(module.time, "perf_counter", lambda: next(ticks))
    with caplog.at_level(logging.INFO):
        if fail:
            with pytest.raises(RuntimeError, match="model failed"):
                engine._observed_scan(detector, Rect(0, 0, 1920, 1080), full=True, reason="manual", check_blank=True)
        else:
            assert engine._observed_scan(detector, Rect(0, 0, 1920, 1080), full=True, reason="manual", check_blank=True) is None
    assert bool(calls) is not blank
    assert ("outcome=error" if fail else "outcome=blank") in caplog.text


def test_existing_full_scan_cadence_is_preserved(monkeypatch):
    from lolhex.engine import Engine
    from lolhex.vision import window
    import lolhex.engine as module
    clock = [100.0]
    full_starts, strip_starts = [], []
    class Stop:
        def is_set(self): return clock[0] >= 180
        def wait(self, delay): clock[0] += delay
    detector = SimpleNamespace(ensure_calibration=lambda *a: None,
        calibrated_for=lambda *a: True, confirmed_for=lambda *a: True)
    def scan(det, rect, *, full, reason, **kwargs):
        if full:
            full_starts.append(clock[0])
            clock[0] += 15
        else:
            strip_starts.append(clock[0])
        return None
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(window, "find_game_window", lambda: 1)
    monkeypatch.setattr(window, "client_rect", lambda _: window.Rect(0, 0, 1920, 1080))
    monkeypatch.setattr(window, "is_foreground", lambda _: True)
    monkeypatch.setattr(window, "lower_thread_priority", lambda: None)
    engine = Engine.__new__(Engine)
    engine._stop, engine._rescan, engine._detector = Stop(), threading.Event(), detector
    engine.state = {"phase": "ingame", "vision": {}}
    engine.settings = SimpleNamespace(scan_interval_ms=300, idle_scan_ms=300, speed_factor=1, bootstrap_interval_s=3, fallback_scan_s=30)
    engine._observed_scan = scan
    engine._sync_augment_tables = lambda det: None  # 本测试只控制扫描节奏
    engine._vision_loop()
    assert len(full_starts) == 3  # 按 fallback_scan_s（这里设 30）start-to-start，不是 15 s + 60 s backoff.
    assert all(30 < b-a < 31 for a, b in zip(full_starts, full_starts[1:]))
    assert not any(full_starts[0] < t < full_starts[0]+15 for t in strip_starts)


def test_capture_failure_is_not_reported_as_ocr_time(monkeypatch, caplog):
    from lolhex.engine import Engine
    from lolhex.vision import capture
    from lolhex.vision.window import Rect
    import lolhex.engine as module
    def fail(*args): raise RuntimeError("capture failed")
    monkeypatch.setattr(capture, "grab", fail)
    ticks = iter([100, 102])
    monkeypatch.setattr(module.time, "perf_counter", lambda: next(ticks))
    engine = Engine.__new__(Engine)
    detector = SimpleNamespace(confirmed_for=lambda *args: False)
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError, match="capture failed"):
        engine._observed_scan(detector, Rect(0, 0, 1920, 1080), full=True, reason="fallback")
    assert "capture=2000ms work=0ms" in caplog.text and "stage=capture" in caplog.text


def test_runtime_context_available_without_debug(caplog):
    from lolhex.diagnostics import runtime_context
    from lolhex.config import Settings
    with caplog.at_level(logging.INFO):
        runtime_context(Settings())
    assert "运行环境" in caplog.text and "识别语言=" in caplog.text


def test_title_and_full_model_logs_have_independent_limits(caplog):
    from lolhex.vision.ocr import OCR
    ocr = OCR.__new__(OCR)
    image = np.zeros((48, 448, 3), np.uint8)
    result = SimpleNamespace(txts=None)
    with caplog.at_level(logging.INFO):
        for kind, start in [("整块", 100), ("标题", 110), ("整块", 120), ("标题", 130)]:
            ocr._profile(kind, image, result, start, start+.1, start+2)
    assert len(caplog.records) == 2
    assert "OCR整块耗时" in caplog.text and "OCR标题耗时" in caplog.text


def test_recognition_timing_is_kept_when_detector_is_disabled(caplog):
    from lolhex.vision.ocr import OCR
    ocr = OCR.__new__(OCR)
    with caplog.at_level(logging.INFO):
        ocr._profile("标题", np.zeros((48, 448, 3), np.uint8),
                     SimpleNamespace(txts=None, elapse_list=[None, None, 1.5]), 100, 100.1, 102)
    assert "检测=unknown毫秒 认字=1500毫秒" in caplog.text


def test_rec_only_public_output_uses_its_elapse_field(caplog):
    from lolhex.vision.ocr import OCR
    ocr = OCR.__new__(OCR)
    with caplog.at_level(logging.INFO):
        ocr._profile("标题", np.zeros((48, 448, 3), np.uint8),
                     SimpleNamespace(txts=None, elapse=1.7), 100, 100.1, 102)
    assert "检测=unknown毫秒 认字=1700毫秒" in caplog.text


def test_full_and_strip_scan_logs_have_independent_limits(monkeypatch, caplog):
    from lolhex.engine import Engine
    from lolhex.vision import capture
    from lolhex.vision.window import Rect
    import lolhex.engine as module
    ticks = iter([100, 100.1, 102, 110, 110.1, 112, 120, 120.1, 122])
    monkeypatch.setattr(module.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(capture, "grab", lambda *args: np.zeros((48, 448, 3), np.uint8))
    detector = SimpleNamespace(scan_full=lambda *a: None, check_strip=lambda *a: None,
        strip_rect=lambda rect: rect, confirmed_for=lambda *a: True)
    engine = Engine.__new__(Engine)
    with caplog.at_level(logging.INFO):
        for full in [True, False, True]:
            engine._observed_scan(detector, Rect(0, 0, 1920, 1080), full=full, reason="test")
    assert len(caplog.records) == 2
    assert "path=full" in caplog.text and "path=strip" in caplog.text
