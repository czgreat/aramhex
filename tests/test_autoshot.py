"""自动截图：每次三选一、每次选英雄各截一张；存的是不涂掉浮窗的原样截图。"""
import time
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from lolhex.ui import autoshot  # noqa: E402
from lolhex.vision import capture  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    later = []
    monkeypatch.setattr(autoshot.QTimer, "singleShot", lambda ms, fn: later.append(fn))
    class InlineThread:
        def __init__(self, target, args, **kwargs):
            self.target, self.args = target, args
        def start(self):
            self.target(*self.args)
    monkeypatch.setattr(autoshot, "Thread", InlineThread)
    shots = []

    def grab(rect, mask=True):
        shots.append(mask)  # 自动截图要看浮窗摆得对不对：不涂
        return np.full((40, 60, 3), 128, np.uint8) + np.arange(60, dtype=np.uint8)[None, :, None]
    monkeypatch.setattr(capture, "grab", grab)
    engine = SimpleNamespace(settings=SimpleNamespace(debug_mode=True))
    return autoshot.AutoShot(engine), later, shots, engine


def run(later):
    while later:
        later.pop(0)()


def test_one_shot_per_offer_unmasked(env):
    shot, later, shots, engine = env
    snap = {"evaluation": {"x": 1}, "offer": {"slots": ["1", "2", "3"]}}
    shot.tick(snap, "rect", None)
    run(later)
    assert shots == [False]
    shot.tick(snap, "rect", None)  # 同一次三选一不重复截
    run(later)
    assert len(shots) == 1
    shot.tick({**snap, "offer": {"slots": ["4", "5", "6"]}}, "rect", None)  # 刷新后换了三张：再截
    run(later)
    assert len(shots) == 2
    files = sorted(p.name for p in autoshot.shots_dir().glob("*.png"))
    assert len(files) == 2 and all(n.endswith("-三选一.png") for n in files)


def test_champ_select_waits_until_labels_settle(env, monkeypatch):
    shot, later, shots, _ = env
    clock = [100.0]
    monkeypatch.setattr(autoshot.time, "monotonic", lambda: clock[0])
    def cs(n):
        return {"champ_select": {"team": [{"hero_id": str(i), "grade": "A"} for i in range(n)], "bench": []}}
    shot.tick(cs(2), None, "client"); run(later)
    clock[0] += 2; shot.tick(cs(4), None, "client"); run(later)   # 还在陆续识别：不截
    assert not shots
    clock[0] += 4; shot.tick(cs(4), None, "client"); run(later)   # 4 个稳定 4 秒：截
    assert len(shots) == 1
    clock[0] += 5; shot.tick(cs(4), None, "client"); run(later)   # 没变化：不重复截
    assert len(shots) == 1
    shot.tick(cs(5), None, "client"); clock[0] += 5; shot.tick(cs(5), None, "client"); run(later)  # 又多标一个：再截
    assert len(shots) == 2
    shot.tick({"champ_select": None}, None, None)                  # 离开选人界面：下一局重新算
    shot.tick(cs(3), None, "client"); clock[0] += 5; shot.tick(cs(3), None, "client"); run(later)
    assert len(shots) == 3


def test_off_by_default_does_nothing(env):
    shot, later, shots, engine = env
    engine.settings.debug_mode = False  # 只在调试模式（debug.txt）下截
    shot.tick({"evaluation": {"x": 1}, "offer": {"slots": ["1", "2", "3"]}}, "rect", None)
    assert not later and not shots


def test_pending_offer_does_not_consume_the_stable_screenshot(env):
    shot, later, shots, _ = env
    offer = {"slots": ["1", "2", "3"], "pending_columns": (False, True, False)}
    shot.tick({"evaluation": {"x": 1}, "offer": offer}, "rect", None)
    assert not later and not shots
    offer["pending_columns"] = (False, False, False)
    shot.tick({"evaluation": {"x": 1}, "offer": offer}, "rect", None)
    run(later)
    assert shots == [False]


def test_filename_uses_capture_completion_before_png_encoding(env, monkeypatch):
    from datetime import datetime, timedelta
    import cv2

    shot, later, shots, _ = env
    clock = [datetime(2026, 10, 30, 12, 0, 0)]
    class Clock:
        @staticmethod
        def now():
            return clock[0]
    original = cv2.imencode
    def encode(*args):
        clock[0] += timedelta(seconds=2)
        return original(*args)
    monkeypatch.setattr(autoshot, 'datetime', Clock)
    monkeypatch.setattr(cv2, 'imencode', encode)
    shot.tick({'evaluation': {'x': 1}, 'offer': {'slots': ['1', '2', '3']}}, 'rect', None)
    run(later)
    assert shots == [False]
    assert [p.name for p in autoshot.shots_dir().glob('*.png')] == ['20261030-120000-000-三选一.png']


def test_png_failure_releases_busy_without_incomplete_file(env, monkeypatch):
    import cv2
    shot, later, _, _ = env
    monkeypatch.setattr(cv2, 'imencode', lambda *args: (False, None))
    shot.tick({'evaluation': {'x': 1}, 'offer': {'slots': ['1', '2', '3']}}, 'rect', None)
    run(later)
    assert not shot._busy and not list(autoshot.shots_dir().glob('*.png'))


def test_screenshot_thread_closes_its_capture_instance(tmp_path, monkeypatch):
    """每张截图一个新线程，线程里开的 mss 要关掉；不关的话每张都留一块 GDI 位图（4K 约 33MB）。"""
    import threading

    import mss

    from lolhex.vision.window import Rect
    opened = []

    class FakeMSS:
        def __init__(self):
            self.closed = False
            opened.append(self)

        def grab(self, mon):
            img = np.full((mon["height"], mon["width"], 4), 128, np.uint8)
            img[:, :, 0] += np.arange(mon["width"], dtype=np.uint8)[None, :]
            return img

        def close(self):
            self.closed = True
    monkeypatch.setattr(mss, "MSS", FakeMSS)
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    monkeypatch.setattr(autoshot.QTimer, "singleShot", lambda ms, fn: fn())
    shot = autoshot.AutoShot(SimpleNamespace(settings=SimpleNamespace(debug_mode=True)))
    for slots in (['1', '2', '3'], ['4', '5', '6']):
        shot.tick({'evaluation': {'x': 1}, 'offer': {'slots': slots}}, Rect(0, 0, 60, 40), None)
        for t in threading.enumerate():
            if t.name == "debug-screenshot":
                t.join(5)
        assert not shot._busy
    assert len(opened) == 2 and all(m.closed for m in opened)
    assert len(list(autoshot.shots_dir().glob('*.png'))) == 2
