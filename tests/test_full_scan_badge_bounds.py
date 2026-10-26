"""从整块 OCR 到实际绘制，验证标题保护与窄条接续。"""

from conftest import render_in_game
import numpy as np
import pytest


@pytest.mark.parametrize("size", [(1366, 768), (1920, 1080), (2560, 1440), (3440, 1440), (3840, 2160)])
@pytest.mark.parametrize("count", [2, 3])
def test_full_scan_does_not_cover_titles_and_complete_offer_stays_put(size, count, store, tmp_path, monkeypatch):
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui.overlay import BadgeLayer
    from lolhex.vision import window
    from lolhex.vision.detector import AugmentDetector, BAND, DETECT_MAX_W
    from lolhex.vision.matcher import NameMatcher
    from lolhex.vision.ocr import TextLine
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(window, "_drawn", {})
    width, height = size
    unit = height / 1080
    origin = (-640, 120)
    names = ["炽燃利息", "虚幻武器", "泰坦的坚决"]
    ids = ["2128", "1029", "1373"]
    raw_top = round(450 * unit)
    raw_bottom = round(480 * unit)
    band_x, band_y = int(BAND[0]*width), int(BAND[1]*height)
    scale = min(1.0, DETECT_MAX_W / (int(BAND[2]*width)-band_x))
    class OCR:
        def detect(self, image):
            result = []
            for i, name in enumerate(names[:count]):
                cx = width/2+(i-1)*400*unit
                box = tuple(int(v*scale) for v in (cx-80*unit-band_x, raw_top-band_y,
                                                   cx+80*unit-band_x, raw_bottom-band_y))
                result.append(TextLine(name, 1, box))
            return result

        def recognize(self, image):
            name = names[self.index]
            self.index += 1
            return name, 1

        index = 0
    detector = AugmentDetector(NameMatcher(dict(zip(ids, names))), ocr=OCR())
    offer = detector.scan_full(np.zeros((height, width, 3), np.uint8), origin)
    assert offer and offer.matched() == count
    assert offer.padded == (count == 3)
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase="ingame")
    engine._on_offer(offer)
    assert engine.snapshot()["offer"]["padded"] == offer.padded
    layer = BadgeLayer()
    rect = window.Rect(*origin, width, height)

    def draw(current):
        layer.update_from(current, rect)
        canvas = QImage(width, height, QImage.Format_ARGB32)
        canvas.fill(0)
        render_in_game(layer, canvas, *origin)
        return window.drawn_rects()

    first = draw(engine.snapshot())
    assert len(first) == count
    assert all(r.top+r.height <= origin[1]+raw_top for r in first)
    if count == 3:
        sr = detector.strip_rect(rect)
        following = detector.check_strip(np.zeros((sr.height, sr.width, 3), np.uint8), rect)
        assert following and following.boxes == offer.boxes and following.padded
        # 同一组卡不会重算；定位框属性也必须随新的结果保留。
        engine._on_offer(following)
        assert draw(engine.snapshot()) == first
        # 热缓存里只有 1px 的旧水平偏移时，重校准也必须使下一帧更新位置。
        from lolhex.vision.detector import Calibration
        detector.calibration = Calibration(
            [(a+1/width, b, c+1/width, d) for a,b,c,d in detector.calibration.boxes], size)
        detector._last_fp = None
        detector.ocr.index = 0
        old = detector.check_strip(np.zeros((sr.height, sr.width, 3), np.uint8), rect)
        recalibrated = detector.scan_full(np.zeros((height, width, 3), np.uint8), origin)
        assert old.boxes != recalibrated.boxes
        detector.ocr.index = 0
        new = detector.check_strip(np.zeros((sr.height, sr.width, 3), np.uint8), rect)
        assert new.boxes == recalibrated.boxes
    layer.close()
    app.processEvents()
