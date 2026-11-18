"""四选一从识别到界面：引擎、游戏内标签、主界面和识别节奏。"""

from types import SimpleNamespace

import numpy as np
import pytest
from conftest import render_in_game

from lolhex.vision.detector import Offer


def test_four_card_offer_reaches_labels_and_main_window(store, tmp_path, monkeypatch):
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui.overlay import BadgeLayer
    from lolhex.vision import window
    from lolhex.vision.detector import Calibration
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(window, "_drawn", {})
    W, H = 1920, 1080
    ids = ["2128", "1029", "1373", "1238"]
    boxes = [tuple(int(v) for v in b) for b in Calibration.default(W, H, count=4).pixel_boxes(W, H)]
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase="ingame")
    engine._on_offer(Offer(ids, [""] * 4, [100.0] * 4, boxes, padded=True, source="strip"))
    snap = engine.snapshot()
    assert [s["augment_id"] for s in snap["evaluation"]["slots"]] == ids
    assert len(snap["offer"]["pending_columns"]) == 4
    layer = BadgeLayer()
    rect = window.Rect(0, 0, W, H)
    layer.update_from(snap, rect)
    canvas = QImage(W, H, QImage.Format_ARGB32)
    canvas.fill(0)
    render_in_game(layer, canvas)
    assert len(window.drawn_rects()) == 4  # 四张卡各一个标签
    engine.correct_slot(3, "2128")  # 第 4 张也能手动纠正
    assert engine.snapshot()["offer"]["slots"][3] == "2128"
    # 回到三选一：上一组四张的手动纠正不能把第 4 列带进三张卡
    engine._on_offer(Offer(ids[:3], [""] * 3, [100.0] * 3, boxes[:3], padded=True, source="strip"))
    assert len(engine.snapshot()["offer"]["slots"]) == 3


def test_main_window_shows_fourth_card_only_for_four_choice():
    from PySide6.QtWidgets import QApplication, QLabel, QWidget
    from lolhex.ui.mainwindow import MainWindow
    app = QApplication.instance() or QApplication([])
    fake = SimpleNamespace(offer_advice=QLabel(), offer_cards=[QLabel() for _ in range(4)],
                           offer_frames=[QWidget() for _ in range(4)])
    slot = lambda i: {"index": i, "verdict": "ok", "name": f"卡{i}", "rarity": "gold"}
    MainWindow._render_offer(fake, {"advice": "首选", "slots": [slot(i) for i in range(4)]}, True)
    assert [not f.isHidden() for f in fake.offer_frames] == [True] * 4
    assert fake.offer_cards[3].text()
    MainWindow._render_offer(fake, {"advice": "首选", "slots": [slot(i) for i in range(3)]}, True)
    assert [not f.isHidden() for f in fake.offer_frames] == [True, True, True, False]


def _loop(monkeypatch, scan, until, settings=None, det=None):
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.vision import window
    import lolhex.engine as module
    clock = [0.0]

    class Stop:
        def is_set(self):
            return clock[0] >= until

        def wait(self, delay):
            clock[0] += delay

    class Rescan(Stop):
        def clear(self):
            pass

        def is_set(self):
            return False
    det = det or SimpleNamespace(ensure_calibration=lambda *a: None, calibrated_for=lambda *a: True,
                                 confirmed_for=lambda *a: True)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(window, "find_game_window", lambda: 1)
    monkeypatch.setattr(window, "client_rect", lambda _: window.Rect(0, 0, 1920, 1080))
    monkeypatch.setattr(window, "is_foreground", lambda _: True)
    monkeypatch.setattr(window, "set_vision_thread", lambda *a: False)
    engine = Engine.__new__(Engine)
    engine._stop, engine._rescan, engine._detector = Stop(), Rescan(), det
    engine.state = {"phase": "ingame", "vision": {}}
    engine.settings = settings or Settings()
    engine._observed_scan = lambda det, rect, *, full=False, reason, **kw: scan(clock[0], full, reason)
    engine._on_offer = engine._save_offer_shot = engine._log_vision_cost = lambda *a: None
    engine._scan_discarded = False
    engine._sync_augment_tables = lambda det: None
    engine._vision_loop()


def test_full_scan_every_five_seconds_until_the_first_offer_of_the_game(monkeypatch):
    full = []
    four = Offer(["1", "2", "3", "4"], [""] * 4, [100] * 4, [None] * 4)

    def scan(now, is_full, reason):
        if is_full:
            full.append((round(now, 1), reason))
            return four if now >= 20 else None  # 四选一要靠整块扫描找到
        return four if any(r == "fallback" and t >= 20 for t, r in full) else None
    _loop(monkeypatch, scan, until=200)
    before = [t for t, r in full if t < 20]
    assert len(before) >= 3 and all(4.9 < b - a < 5.5 for a, b in zip(before, before[1:]))
    assert not [t for t, r in full if t > 26]  # 标题条接上后照常放慢


def test_partial_first_read_is_checked_once_by_a_full_scan(monkeypatch):
    full = []
    partial = Offer(["1", None, "3"], [""] * 3, [100] * 3, [None] * 3)
    complete = Offer(["1", "2", "3"], [""] * 3, [100] * 3, [None] * 3)
    reads = {"strip": partial}

    def scan(now, is_full, reason):
        if is_full:
            full.append((round(now, 1), reason))
            return None
        return reads["strip"] if now >= 1 else None
    _loop(monkeypatch, scan, until=60)
    assert [r for _, r in full] == ["layout"]  # 第一次认出但没认全：核对一次，之后不再反复
    full.clear()
    reads["strip"] = complete
    _loop(monkeypatch, scan, until=60)
    assert not full  # 第一次就认全了：不用核对


def test_half_names_trigger_a_full_scan_right_away(monkeypatch):
    """上一局三选一的定位读到四选一的半截名字：马上整块扫描，不等 5 秒的兜底；之后至少隔 3 秒再来。"""
    import lolhex.engine as module
    full = []
    four = Offer(["1", "2", "3", "4"], [""] * 4, [100] * 4, [None] * 4)
    state = {"suspect": False, "found": False}

    class Det(SimpleNamespace):
        @property
        def layout_suspect(self):
            return state["suspect"]

        def clear_layout_suspect(self):
            state["suspect"] = False

    def scan(now, is_full, reason):
        if is_full:
            full.append((round(now, 2), reason))
            state["found"] = state["found"] or (reason == "garbled" and now >= 13)
            return four if state["found"] else None
        if 7.2 <= now and not state["found"]:
            state["suspect"] = True  # 卡出来了，但定位不对：每次只读到半截
        return four if state["found"] else None

    det = Det(ensure_calibration=lambda *a: None, calibrated_for=lambda *a: True, confirmed_for=lambda *a: True)
    _loop(monkeypatch, scan, until=30, det=det)
    garbled = [t for t, r in full if r == "garbled"]
    assert garbled and 7.2 <= garbled[0] < 7.8  # 不等到 10 秒的兜底
    assert all(b - a >= module.Settings.garbled_rescan_s for a, b in zip(garbled, garbled[1:]))
    assert not [t for t, r in full if r == "fallback" and 7.2 <= t < 13]
