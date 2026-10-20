"""三选一标签：大小跟游戏画面走（不跟 Windows 缩放走），可以放卡片上方或下方；双开判断（无界面模式运行）。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtGui import QColor, QImage  # noqa: E402

from lolhex.vision.window import Rect  # noqa: E402


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_unit_follows_game_height():
    from lolhex.ui.overlay import _unit
    assert _unit(1080) == 1 and _unit(1440) == pytest.approx(4 / 3) and _unit(2160) == 2
    assert _unit(100) == 0.6 and _unit(9999) == 3.0


def _wait(ms: int) -> None:
    from PySide6.QtTest import QTest
    QTest.qWait(ms)


def _badge_rows(position: str, monkeypatch, desc_to: int = 560, wait_ms: int = 800) -> list[int]:
    """1920x1080 游戏、一张卡标题在 y=430..470、描述文字写到 desc_to：画出来的标签（绿边）出现在哪几行。"""
    import numpy as np

    from lolhex.ui import overlay
    from lolhex.vision import capture
    app = _app()
    frame = np.zeros((1080, 1920, 3), np.uint8)
    for y in range(490, desc_to, 20):                # 一行一行的描述文字（竖条纹当笔画）
        frame[y:y + 3, 760:940:4] = 255
    monkeypatch.setattr(capture, "grab", lambda rect: frame)
    layer = overlay.BadgeLayer()
    snap = {"settings": {"badge_position": position},
            "offer": {"slots": ["1"], "boxes": [[700, 430, 1000, 470], None, None]},
            "evaluation": {"slots": [{"index": 0, "verdict": "best", "score": 2.0}]}}
    layer.update_from(snap, Rect(0, 0, 1920, 1080))
    app.processEvents()
    _wait(wait_ms)                                   # 下方模式：量两次描述位置（隔 0.6 秒）
    img = QImage(1920, 1080, QImage.Format_ARGB32)
    img.fill(0)
    layer.render(img)
    green = QColor(95, 211, 141).rgb()
    return [y for y in range(0, 1080, 2) if any(img.pixel(x, y) == green for x in range(760, 940, 3))]


def test_badge_top_and_bottom(monkeypatch):
    top, bottom = _badge_rows("top", monkeypatch), _badge_rows("bottom", monkeypatch)
    assert top and max(top) < 430                    # 标题上方
    assert bottom and min(bottom) > 560               # 描述下面，不压字
    assert max(bottom) <= 450 + 236


def test_badge_back_on_top_when_description_long(monkeypatch):
    rows = _badge_rows("bottom", monkeypatch, desc_to=640)  # 描述写到很下面：放不下，回到标题上方
    assert rows and max(rows) < 430


def test_bottom_waits_for_second_measure(monkeypatch):
    """下方模式两次都量完才画：先画了再挪到上方会跳一下（玩家实测看到先下后上）。"""
    assert _badge_rows("bottom", monkeypatch, wait_ms=100) == []
    assert _badge_rows("top", monkeypatch, wait_ms=0)            # 上方模式不等


def test_bottom_room_counts_two_lines(monkeypatch):
    """标签现在只有一行也按两行算：数据加载完多出一行时，不会从下方跳回上方。"""
    rows = _badge_rows("bottom", monkeypatch, desc_to=620)  # 一行放得下、两行放不下
    assert rows and max(rows) < 430


def test_admin_instance_counts_as_running(monkeypatch):
    import ctypes
    import sys

    from lolhex.ui import single_instance as si

    class K:
        def __init__(self):
            self.CreateMutexW = lambda *a: None
            self.WaitForSingleObject = lambda *a: 0
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: K(), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(si, "_held", [])
    monkeypatch.setattr(si, "denied", False)
    assert si.acquire("x") is False and si.denied   # 管理员身份的助手在跑：没权限也算有人在跑
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1450, raising=False)
    assert si.acquire("x") is True                  # 别的错误：判断不了就照常启动


def test_preview_widget_paints():
    _app()
    from lolhex.ui.badgepos import BadgePosPreview
    w = BadgePosPreview("bottom")
    assert not w.grab().isNull()


def test_desc_end_ignores_sparkles():
    import numpy as np

    from lolhex.vision.cardspace import desc_end
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[500:514, 800:900:3] = 200                  # 一行字：标题中心（450）往下 50–64
    frame[520:530, 800:900:3] = 120                  # 灰色小字也算：往下 70–80
    frame[640, 840:847:2] = 255                      # 卡片边上的小星星：一行、几个像素
    frame[600:640, 760:940] = 90                     # 平滑的亮底色不算
    assert desc_end(frame, (700, 430, 1000, 470)) == 79
    assert desc_end(frame, None) is None
