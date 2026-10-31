"""Replay a malformed 3840x2160 calibration seen in a real log."""
import json
import numpy as np
import pytest

from lolhex.vision.detector import AugmentDetector, Calibration, BAND, DETECT_MAX_W
from lolhex.vision.matcher import NameMatcher
from lolhex.vision.ocr import TextLine


BAD_BOXES=[(.21689713541666666,.3851851851851852,.3844049479166667,.4351851851851852),
           (.29619401041666665,.3851851851851852,.4637018229166667,.4351851851851852),
           (.6157252604166668,.3851851851851852,.7832330729166667,.4351851851851852)]


def detector_with_centers(centers,path=None):
    width,height=3840,2160
    x0,y0=int(BAND[0]*width),int(BAND[1]*height)
    scale=DETECT_MAX_W/(int(BAND[2]*width)-x0)
    class OCR:
        def detect(self,image):
            return [TextLine(name,1,tuple(int(v*scale) for v in
                (cx-80-x0,865-y0,cx+80-x0,905-y0)))
                for name,cx in zip(["甲甲","乙乙","丙丙"],centers)]
    return AugmentDetector(NameMatcher({"0":"甲甲","1":"乙乙","2":"丙丙"}),
                           calib_path=path,ocr=OCR())


def test_recorded_left_shift_is_rejected_before_publish_or_save(tmp_path,caplog):
    path=tmp_path/'calibration.json'
    good=Calibration.default(3840,2160)
    good.guess=False
    path.write_text(json.dumps({'boxes':good.boxes,'size':good.size}),encoding='utf-8')
    before=path.read_bytes()
    centers=[(b[0]+b[2])*3840/2 for b in BAD_BOXES]
    det=detector_with_centers(centers,path)
    original=det.calibration
    det._low_match_reads=1
    assert det.scan_full(np.zeros((2160,3840,3),np.uint8)) is None
    assert det.calibration is original
    assert det._low_match_reads==1
    assert path.read_bytes()==before
    assert '拒绝三选一校准' in caplog.text


def test_recorded_bad_saved_calibration_is_not_reused(tmp_path):
    path=tmp_path/'calibration.json'
    path.write_text(json.dumps({'boxes':BAD_BOXES,'size':[3840,2160]}),encoding='utf-8')
    det=detector_with_centers([1180,1920,2660],path)
    assert det.calibration is None
    # The original is preserved for diagnosis until a valid full scan replaces it.
    assert json.loads(path.read_text(encoding='utf-8'))['boxes'][1][0]==BAD_BOXES[1][0]
    assert det.scan_full(np.zeros((2160,3840,3),np.uint8)).matched()==3
    assert det.calibration.valid()


@pytest.mark.parametrize('centers',[[1180,1920,2660],[1180,1830,2660],[1180,2010,2660]])
def test_safe_unequal_spacing_is_still_allowed(centers):
    det=detector_with_centers(centers)
    assert det.scan_full(np.zeros((2160,3840,3),np.uint8)).matched()==3
    assert det.calibration.valid()


@pytest.mark.parametrize('box',[(float('nan'),.3,.4,.4),(-.1,.3,.2,.4),
    (.2,.3,.1,.4),(.2,.3,.4,1.1)])
def test_invalid_box_is_rejected(box):
    calibration=Calibration([box,(.45,.3,.55,.4),(.65,.3,.75,.4)],(3840,2160))
    assert not calibration.valid()


@pytest.mark.parametrize('size',[(float('nan'),2160),(3840,float('inf')),(0,2160)])
def test_invalid_size_is_rejected(size):
    assert not Calibration(Calibration.default(3840,2160).boxes,size).valid()


def test_subpixel_empty_crop_is_rejected():
    boxes=[(.2,.3,.20000001,.4),(.45,.3,.55,.4),(.65,.3,.75,.4)]
    assert not Calibration(boxes,(3840,2160)).valid()
