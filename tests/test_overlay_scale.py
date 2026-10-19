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


def _badge_rows(position: str) -> list[int]:
    """1920x1080 游戏、一张卡标题在 y=430..470：画出来的标签（绿边）出现在哪几行。"""
    _app()
    from lolhex.ui.overlay import BadgeLayer
    layer = BadgeLayer()
    snap = {"settings": {"badge_position": position},
            "offer": {"boxes": [[700, 430, 1000, 470], None, None]},
            "evaluation": {"slots": [{"index": 0, "verdict": "best", "score": 2.0}]}}
    layer.update_from(snap, Rect(0, 0, 1920, 1080))
    img = QImage(1920, 1080, QImage.Format_ARGB32)
    img.fill(0)
    layer.render(img)
    green = QColor(95, 211, 141).rgb()
    return [y for y in range(0, 1080, 2) if any(img.pixel(x, y) == green for x in range(760, 940, 3))]


def test_badge_top_and_bottom():
    top, bottom = _badge_rows("top"), _badge_rows("bottom")
    assert top and max(top) < 430                    # 标题上方
    assert bottom and min(bottom) > 470 + 100        # 描述下面
    assert max(bottom) < 450 + 197 + 60


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
