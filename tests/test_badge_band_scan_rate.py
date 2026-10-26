"""双屏选海克斯掉帧（10-24）：三选一标签层只盖标签那一块、碰不到屏幕边缘；没在选海克斯时可以慢扫（云控）。"""

import pytest

from conftest import render_in_game


@pytest.mark.parametrize("size", [(1920, 1080), (2560, 1440), (3840, 2160)])
def test_badge_layer_stays_off_screen_edges(size):
    from PySide6.QtWidgets import QApplication
    from lolhex.ui.overlay import BadgeLayer
    from lolhex.vision import window
    app = QApplication.instance() or QApplication([])
    w, h = size
    k = h / 1080
    game = window.Rect(0, 0, w, h)
    # 三张卡标题框（国服默认位置，按窗口比例）
    boxes = [[int(w * a), int(h * b), int(w * c), int(h * d)] for a, b, c, d in
             [(0.2309, 0.3869, 0.3923, 0.4335), (0.4231, 0.3869, 0.5845, 0.4335), (0.6152, 0.3869, 0.7767, 0.4335)]]
    layer = BadgeLayer()
    band = layer.band(game, boxes)
    edge = BadgeLayer.EDGE
    assert band.left >= edge and band.top >= edge and band.right <= w - edge and band.bottom <= h - edge
    assert band.width * band.height < w * h * 0.6  # 不再铺满整个画面
    slots = [{"index": i, "verdict": "best", "score": 2, "item_hint": {"source": "stats", "items": [
        {"item": "3135", "name": "虚空之杖", "win": .55, "games": 1000}]}} for i in range(3)]
    for position in ("top", "bottom"):
        layer.update_from({"settings": {"badge_position": position}, "offer": {"boxes": boxes, "padded": True},
                           "evaluation": {"slots": slots}}, game)
        app.processEvents()
        from PySide6.QtGui import QImage
        image = QImage(w, h, QImage.Format_ARGB32)
        image.fill(0)
        render_in_game(layer, image)
        drawn = window.drawn_rects()
        assert len(drawn) == 6
        # 标签和装备提示都在层里，没被裁掉
        assert all(band.left <= r.left and r.left + r.width <= band.right + 3 * k
                   and band.top - 3 <= r.top and r.top + r.height <= band.bottom + 3 for r in drawn), (position, band, drawn)
    layer.hide()


def test_band_falls_back_inside_game_when_boxes_missing():
    from lolhex.ui.overlay import BadgeLayer
    from lolhex.vision import window
    band = BadgeLayer.band(BadgeLayer.__new__(BadgeLayer), window.Rect(100, 50, 2560, 1440), [None, None, None])
    e = BadgeLayer.EDGE
    assert (band.left, band.top, band.right, band.bottom) == (100 + e, 50 + e, 2660 - e, 1490 - e)


def test_scan_slow_until_offer_then_fast_for_a_while(tmp_path):
    from lolhex.config import Settings
    from lolhex.engine import Engine
    engine = Engine.__new__(Engine)
    engine.settings = Settings()
    hold = Engine.ACTIVE_HOLD_S
    assert engine._strip_step(False, float("inf")) == pytest.approx(0.3)      # 默认不慢扫
    engine.settings.idle_scan_ms = 1000                                        # 云控调成平时 1 秒
    assert engine._strip_step(False, float("inf")) == pytest.approx(1.0)      # 平时 1 秒
    assert engine._strip_step(True, 0) == pytest.approx(0.3)                  # 三选一开着
    assert engine._strip_step(False, hold - 1) == pytest.approx(0.3)          # 刚关掉几秒内
    assert engine._strip_step(False, hold + 1) == pytest.approx(1.0)
    engine.settings.speed = "saver"
    assert engine._strip_step(False, hold + 1) == pytest.approx(2.0)          # 省电档照样翻倍
    engine.settings.speed = "standard"
    engine.settings.scan_interval_ms = 1500                                    # 快扫本来就比慢扫慢：不再拖慢
    assert engine._strip_step(False, hold + 1) == pytest.approx(1.5)
