"""Discarded OCR still consumed time and must remain visible in normal logs."""
import logging
import pytest

from test_scan_geometry_freshness import setup_scan
from lolhex.vision.window import Rect


@pytest.mark.parametrize('full',[False,True])
def test_stale_after_long_ocr_records_work_without_publishing(monkeypatch,store,caplog,full):
    import lolhex.engine as module
    engine,det,current,captures,cleared=setup_scan(monkeypatch,store)
    old=current['rect'];original=det.scan_full
    def move(*args):
        result=original(*args)
        current['rect']=Rect(250,50,3840,2160)
        return result
    det.scan_full=det.check_strip=move
    ticks=iter([100,100.2,115.2])
    monkeypatch.setattr(module.time,'perf_counter',lambda:next(ticks))
    with caplog.at_level(logging.INFO):
        assert engine._observed_scan(det,old,full=full,reason='geometry-test',hwnd=7) is None
    assert engine.state['offer'] is None and cleared==[True]
    assert 'capture=200ms work=15000ms total=15200ms outcome=stale stage=work' in caplog.text


def test_stale_before_capture_records_elapsed_without_inventing_ocr(monkeypatch,store,caplog):
    import lolhex.engine as module
    engine,det,current,captures,cleared=setup_scan(monkeypatch,store)
    current['hwnd']=8
    ticks=iter([100,102.5])
    monkeypatch.setattr(module.time,'perf_counter',lambda:next(ticks))
    with caplog.at_level(logging.INFO):
        assert engine._observed_scan(det,current['rect'],full=True,reason='window-test',hwnd=7) is None
    assert captures==[] and cleared==[True]
    assert 'capture=2500ms work=0ms total=2500ms outcome=stale stage=capture' in caplog.text
