import numpy as np

from lolhex.vision.detector import AugmentDetector
from lolhex.vision.matcher import NameMatcher
from lolhex.vision.window import Rect


def setup_detector(monkeypatch):
    import lolhex.vision.detector as module
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    class OCR:
        calls = 0
        values = ["甲甲", "乙乙", "丙丙"]
        def recognize(self, image):
            self.calls += 1
            return self.values.pop(0), 1
    ocr = OCR()
    det = AugmentDetector(NameMatcher({str(i): n for i,n in enumerate(["甲甲", "乙乙", "丙丙", "丁丁"])}), ocr=ocr)
    rect = Rect(0, 0, 1920, 1080)
    det.ensure_calibration(1920,1080)
    sr = det.strip_rect(rect)
    strip = np.zeros((sr.height,sr.width,3),np.uint8)
    det.check_strip(strip,rect)
    return det, ocr, clock, rect, strip


def test_only_changed_column_is_confirmed_by_new_ocr(monkeypatch):
    det,ocr,clock,rect,strip=setup_detector(monkeypatch)
    pb=det.calibration.pixel_boxes(rect.width,rect.height)
    sx=min(b[0] for b in pb)
    strip[:,pb[1][0]-sx:pb[1][2]-sx]=200
    ocr.values=["丁丁","丁丁"]
    assert det.check_strip(strip,rect).slots==["0","1","2"]
    assert ocr.calls==4
    clock[0]+=.3
    assert det.check_strip(strip,rect).slots==["0","3","2"]
    assert ocr.calls==5
    cached=det.check_strip(strip,rect)
    assert cached.source=="cache" and ocr.calls==5


def test_short_blank_column_does_not_change_recommendations(monkeypatch):
    det,ocr,clock,rect,strip=setup_detector(monkeypatch)
    det._cells[1]["at"]-=det.recheck_seconds+1
    ocr.values=["", "乙乙"]
    assert det.check_strip(strip,rect).slots==["0","1","2"]
    clock[0]+=.3
    assert det.check_strip(strip,rect).slots==["0","1","2"]


def test_static_active_cache_rechecks_but_empty_cache_does_not(monkeypatch):
    det,ocr,clock,rect,strip=setup_detector(monkeypatch)
    clock[0]+=det.recheck_seconds+.1
    ocr.values=["甲甲","乙乙","丙丙"]
    for _ in range(3):
        det.check_strip(strip,rect)
        clock[0]+=.3
    assert ocr.calls==6
    det.clear_tracking()
    ocr.values=["", "", ""]
    assert det.check_strip(strip,rect) is None
    calls=ocr.calls
    clock[0]+=30
    assert det.check_strip(strip,rect) is None
    assert ocr.calls==calls


def test_fuzzy_ties_are_unknown_but_full_names_keep_identity():
    matcher=NameMatcher({"1":"更万用的瞄准镜","2":"最万用的瞄准镜",
                         "3":"循环往复","4":"终极循环往复"},min_margin=6)
    assert matcher.match("万用的瞄准镜")[0] is None
    assert matcher.match("终极循环往复")==("4",100)
    assert matcher.match("循环往复")==("3",100)  # 合法短名不能靠提高阈值消除，需要画面证据。


def test_persistent_unknown_column_does_not_run_ocr_every_tick(monkeypatch):
    det,ocr,clock,rect,strip=setup_detector(monkeypatch)
    det._cells[1]["at"]-=det.recheck_seconds+1
    ocr.values=["", ""]
    det.check_strip(strip,rect)
    clock[0]+=.3
    assert det.check_strip(strip,rect).slots==["0",None,"2"]
    calls=ocr.calls
    for _ in range(3):
        clock[0]+=.3
        det.check_strip(strip,rect)
    assert ocr.calls==calls


def test_tracking_is_cleared_without_losing_calibration(monkeypatch):
    det,ocr,clock,rect,strip=setup_detector(monkeypatch)
    calibration=det.calibration
    det.clear_tracking()
    assert det.calibration is calibration
    assert det._stable is None and det._last_result is None
    ocr.values=["丁丁","乙乙","丙丙"]
    assert det.check_strip(strip,rect).slots==["3","1","2"]
