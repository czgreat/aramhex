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