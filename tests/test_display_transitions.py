"""原生 Qt 窗口缩放与连续界面状态验证；截图是测试画面，不代表游戏实录。"""

from conftest import render_in_game
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("size", [(1366, 768), (1920, 1080), (2560, 1440), (3440, 1440), (3840, 2160)])
@pytest.mark.parametrize("position", ["top", "bottom"])
def test_badges_follow_physical_pixels_at_current_qt_scale(size, position, tmp_path):
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication
    from lolhex.ui.overlay import BadgeLayer
    from lolhex.vision import window
    app = QApplication.instance() or QApplication([])
    width, height = size
    factor = height / 1080
    origin = (-640, 120)
    icon = QImage(24, 24, QImage.Format_ARGB32)
    icon.fill(QColor("yellow"))
    icon_path = str(tmp_path / "icon.png")
    icon.save(icon_path)
    layer = BadgeLayer()
    layer.icons = SimpleNamespace(local=lambda *args: icon_path)
    cx = width / 2
    box = [int(origin[0]+cx-150*factor), int(origin[1]+430*factor),
           int(origin[0]+cx+150*factor), int(origin[1]+470*factor)]
    row = {"index": 0, "verdict": "best", "score": 2, "win": .56,
           "item_hint": {"source": "stats", "items": [
               {"item": str(i), "name": n, "win": .55, "games": 1000}
               for i, n in [(3135, "虚空之杖"), (4645, "影焰"), (3072, "饮血剑")]]}}
    layer.update_from({"settings": {"badge_position": position}, "offer": {"boxes": [box], "padded": True},
                       "evaluation": {"slots": [row]}}, window.Rect(*origin, width, height))
    app.processEvents()
    image = QImage(width, height, QImage.Format_ARGB32)
    image.setDevicePixelRatio(layer.devicePixelRatioF())
    image.fill(0)
    render_in_game(layer, image, *origin)
    regions = window.drawn_rects()
    assert len(regions) == 2
    if position == "top":
        badge = regions[0]
        title_padding = (box[3] - box[1]) * 2 / 9
        # 遮罩包含额外 2px 边缘；下移后仍与真实标题文字上沿保持间距。
        assert badge.top + badge.height <= box[1] + title_padding + 2
        assert badge.top + badge.height > box[1] - 4 * factor
    # 按物理像素核对图标已画出，位置与 Windows/Qt 缩放无关。
    yellow = []
    for y in range(0, height, 2):
        for x in range(max(0, int(cx-180*factor)), min(width, int(cx+180*factor)), 2):
            if image.pixelColor(x, y) == QColor("yellow"):
                yellow.append((x, y))
    assert yellow, (size, position, layer.devicePixelRatioF(), layer.size())
    icon_y = sum(y for _, y in yellow)/len(yellow)
    assert (icon_y < 430*factor) == (position == "bottom")
    assert all(origin[0]-3 <= r.left and r.left+r.width <= origin[0]+width+3
               and origin[1]-3 <= r.top and r.top+r.height <= origin[1]+height+3 for r in regions)
    folder = os.environ.get("ARAMHEX_QA_IMAGES")
    if folder and size == (2560, 1440):
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        assert image.save(str(path / f"badge-{width}x{height}-{position}-dpr{layer.devicePixelRatioF():g}.png"))
    layer.close()
    app.processEvents()


def test_main_window_removes_hero_and_recommendation_after_game_end(store, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui.mainwindow import MainWindow
    from lolhex.vision.detector import Offer
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Engine, "_prefetch_hero", lambda *a: None)
    eng = Engine(Settings(), home=tmp_path, store=store)
    window = MainWindow(eng, SimpleNamespace(set_visible=lambda *a: None))
    window.show()
    eng.set_hero("17")
    eng._set(phase="ingame")
    eng._on_offer(Offer(["2128", "1029", "1373"], [], [], [None]*3))
    window.refresh()
    app.processEvents()
    assert "提莫" in window.hero_label.text()
    assert "首选" in window.offer_advice.text()
    eng._on_game_end()
    window.refresh()
    app.processEvents()
    assert "未识别到英雄" in window.hero_label.text()
    assert "还没有选英雄" in window.tab_hero.text()
    assert "未在选海克斯" in window.offer_advice.text()
    assert all(not card.text().strip() for card in window.offer_cards)
    folder = os.environ.get("ARAMHEX_QA_IMAGES")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        assert window.grab().save(str(Path(folder) / f"main-cleared-dpr{window.devicePixelRatioF():g}.png"))
    window.hide()
    window.deleteLater()


@pytest.mark.parametrize("available", [(1366, 728), (1024, 576), (683, 344)])
def test_main_window_fits_small_logical_desktop(store, tmp_path, monkeypatch, available):
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui.mainwindow import MainWindow
    app = QApplication.instance() or QApplication([])
    eng = Engine(Settings(), home=tmp_path, store=store)
    window = MainWindow(eng, SimpleNamespace(set_visible=lambda *a: None))
    rect = QRect(0, 0, *available)
    monkeypatch.setattr(window, "screen", lambda: SimpleNamespace(availableGeometry=lambda: rect))
    window.show_and_raise()
    app.processEvents()
    assert window.width() <= available[0]
    assert window.height() <= available[1]-32
    assert window.centralWidget().widgetResizable()
    window.hide()
    window.deleteLater()
