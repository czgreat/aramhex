"""识别到一半窗口或对局变了：内存里的位置作废，换回本机保存的那份（1.1.3），不退回默认位置。"""
import json
import threading
from types import SimpleNamespace

import numpy as np

from lolhex import cloudsettings
from lolhex.config import Settings
from lolhex.engine import Engine
from lolhex.vision import capture, window
from lolhex.vision.detector import AugmentDetector, Calibration
from lolhex.vision.matcher import NameMatcher

SAVED = [(0.24, 0.376, 0.393, 0.425), (0.422, 0.376, 0.577, 0.425), (0.607, 0.376, 0.761, 0.425)]


def _engine(monkeypatch, store, det, **settings):
    engine = Engine.__new__(Engine)
    engine._lock = threading.RLock()
    engine._scan_epoch = 0
    engine._evaluation_revision = 0
    engine._last_eval_log = None
    engine._manual_hero = False
    engine._data_wake = threading.Event()
    engine._rescan = threading.Event()
    engine.store = store
    engine.settings = Settings(**settings)
    engine.state = {"phase": "ingame", "hero_id": "17", "offer": None, "evaluation": None, "vision": {}}
    engine._detector = det
    current = {"hwnd": 7, "rect": window.Rect(0, 0, 1920, 1080)}
    monkeypatch.setattr(window, "find_game_window", lambda: current["hwnd"])
    monkeypatch.setattr(window, "client_rect", lambda hwnd: current["rect"])
    monkeypatch.setattr(window, "is_foreground", lambda hwnd: True)
    monkeypatch.setattr(capture, "grab", lambda rect: np.zeros((48, 448, 3), np.uint8))
    return engine, current


def _detector(tmp_path, size=(1920, 1080), saved=True):
    path = tmp_path / "calibration.json"
    if saved:
        path.write_text(json.dumps({"boxes": SAVED, "size": list(size)}), encoding="utf-8")
    return AugmentDetector(NameMatcher({"1": "甲甲"}), calib_path=path, ocr=SimpleNamespace())


def _scan_then_change(engine, current, det, change):
    def scan(*a):
        # 在途扫描把内存里的位置改成了从旧画面算出来的（没写盘：画面作废时不提交）
        det.calibration = Calibration([(0.1, 0.1, 0.2, 0.2), (0.3, 0.1, 0.4, 0.2), (0.5, 0.1, 0.6, 0.2)], (1920, 1080))
        if change == "window":
            current["hwnd"] = 8
        elif change == "resize":
            current["rect"] = window.Rect(0, 0, 2560, 1440)
        else:
            engine._clear_selection()
            engine._set(phase="ingame", hero_id="17")
        return None
    det.scan_full = det.check_strip = scan
    assert engine._observed_scan(det, window.Rect(0, 0, 1920, 1080), full=True, reason="test", hwnd=7) is None


def test_discard_restores_saved_position(monkeypatch, store, tmp_path):
    for change in ("window", "session"):
        det = _detector(tmp_path)
        engine, current = _engine(monkeypatch, store, det)
        _scan_then_change(engine, current, det, change)
        assert det.calibration.boxes == [tuple(b) for b in SAVED] and not det.calibration.guess
        assert det.confirmed_for(1920, 1080)  # 下一局直接按本机位置读标题，不用每 3 秒整块扫描
        det.ensure_calibration(1920, 1080)
        assert det.calibration.boxes == [tuple(b) for b in SAVED]


def test_resolution_changed_still_relocates(monkeypatch, store, tmp_path):
    det = _detector(tmp_path)
    engine, current = _engine(monkeypatch, store, det)
    _scan_then_change(engine, current, det, "resize")
    assert not det.calibrated_for(2560, 1440)  # 存的是 1920x1080 的：不会用到新分辨率上
    det.ensure_calibration(2560, 1440)
    assert det.calibration.guess and det.calibration.size == (2560, 1440)


def test_no_saved_file_or_switched_off_falls_back_to_default(monkeypatch, store, tmp_path):
    det = _detector(tmp_path, saved=False)
    engine, current = _engine(monkeypatch, store, det)
    _scan_then_change(engine, current, det, "window")
    assert det.calibration is None
    det = _detector(tmp_path)
    engine, current = _engine(monkeypatch, store, det, calib_restore=False)  # 云控关掉 = 1.1.2 的做法
    _scan_then_change(engine, current, det, "window")
    assert det.calibration is None
    det.ensure_calibration(1920, 1080)
    assert det.calibration.guess


def test_reset_calibration_deletes_file_so_nothing_is_restored(tmp_path):
    det = _detector(tmp_path)
    det.reset_calibration()
    assert not det.restore_calibration() and det.calibration is None


def test_cloud_can_switch_it_off():
    s = Settings()
    assert s.calib_restore is True
    cloudsettings.apply(s, {"settings": [{"id": "t", "force": {"calib_restore": False}}]}, "1.1.3")
    assert s.calib_restore is False
