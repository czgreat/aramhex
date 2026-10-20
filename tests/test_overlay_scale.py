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


def test_long_label_widens_box():
    """还没校准时标题框只有标题文字那么宽：尊我为王那句长标签按字撑开底框，字不出框。"""
    from lolhex.ui import overlay
    app = _app()
    layer = overlay.BadgeLayer()
    snap = {"settings": {"badge_position": "top"},
            "offer": {"slots": ["1"], "boxes": [[900, 430, 1000, 470], None, None]},
            "evaluation": {"slots": [{"index": 0, "verdict": "unknown", "basis": "excluded"}]}}
    layer.update_from(snap, Rect(0, 0, 1920, 1080))
    app.processEvents()
    img = QImage(1920, 1080, QImage.Format_ARGB32)
    img.fill(0)
    layer.render(img)
    y = 430 - 4 - 15                                 # 标签中间一行（只有大字，高 30）
    xs = [x for x in range(0, 1920) if img.pixelColor(x, y).alpha() > 100]
    from PySide6.QtGui import QFont, QFontMetrics
    need = QFontMetrics(QFont("Microsoft YaHei UI", 12, QFont.Bold), layer).horizontalAdvance(overlay.NODATA_NOTE)
    assert xs and max(xs) - min(xs) >= need and abs((max(xs) + min(xs)) / 2 - 950) <= 2


def test_reroll_keeps_bottom_badges(monkeypatch):
    """放下方时刷新一张卡：另外两张（和刷新的这张）标签照常显示，后台重新量；以前三张会一起消失一两秒。"""
    import numpy as np

    from lolhex.ui import overlay
    from lolhex.vision import capture
    app = _app()
    frame = np.zeros((1080, 1920, 3), np.uint8)
    for y in range(490, 560, 20):
        frame[y:y + 3, 760:940:4] = 255
    monkeypatch.setattr(capture, "grab", lambda rect: frame)
    layer = overlay.BadgeLayer()
    boxes = [[700, 430, 1000, 470], [1100, 430, 1400, 470], None]

    def snap(slots):
        return {"settings": {"badge_position": "bottom"}, "offer": {"slots": slots, "boxes": boxes},
                "evaluation": {"slots": [{"index": 0, "verdict": "best", "score": 2.0},
                                         {"index": 1, "verdict": "ok", "score": 1.0}]}}

    def green_rows():
        img = QImage(1920, 1080, QImage.Format_ARGB32)
        img.fill(0)
        layer.render(img)
        green = QColor(95, 211, 141).rgb()
        return [y for y in range(0, 1080, 2) if any(img.pixel(x, y) == green for x in range(760, 940, 3))]

    layer.update_from(snap(["1", "2", None]), Rect(0, 0, 1920, 1080))
    app.processEvents()
    _wait(800)
    before = green_rows()
    assert before and min(before) > 560
    for slots in (["1", None, None], ["1", "3", None]):  # 刷新中间那张：先读不出，再读出新的
        layer.update_from(snap(slots), Rect(0, 0, 1920, 1080))
        app.processEvents()
        assert green_rows() == before                 # 一直在原位，没有消失
    layer.update_from({"settings": {"badge_position": "bottom"}}, Rect(0, 0, 1920, 1080))  # 这组选完了
    layer.update_from(snap(["4", "5", None]), Rect(0, 0, 1920, 1080))  # 下一组：照旧量完才画
    app.processEvents()
    assert green_rows() == []


def test_overlapping_measures_restore_capture_once(monkeypatch):
    """开了「录屏、截图时显示浮窗」：刷新时前后两次量会重叠，等都量完才恢复可截图（不然后一次会截到自己的标签）。"""
    import numpy as np

    from lolhex.ui import overlay
    from lolhex.vision import capture
    app = _app()
    calls = []
    monkeypatch.setattr(overlay.win, "capture_allowed", lambda: True)
    monkeypatch.setattr(overlay.win, "hide_from_capture", lambda h: calls.append("hide"))
    monkeypatch.setattr(overlay.win, "restore_capture", lambda h: calls.append("restore"))
    hidden = []

    def grab(rect):
        hidden.append(calls.count("hide") > calls.count("restore"))  # 截图这一刻标签是不是不会被截到
        return np.zeros((1080, 1920, 3), np.uint8)
    monkeypatch.setattr(capture, "grab", grab)
    layer = overlay.BadgeLayer()
    boxes = [[700, 430, 1000, 470], [1100, 430, 1400, 470], None]
    ev = {"slots": [{"index": 0, "verdict": "best", "score": 2.0}]}
    for slots in (["1", "2", None], ["1", "3", None]):
        layer.update_from({"settings": {"badge_position": "bottom"}, "offer": {"slots": slots, "boxes": boxes},
                           "evaluation": ev}, Rect(0, 0, 1920, 1080))
        app.processEvents()
    _wait(1200)
    assert hidden and all(hidden)                    # 每次截图时都不会截到自己的标签
    assert calls.count("hide") == calls.count("restore") and layer._hiding == 0  # 量完都恢复了
