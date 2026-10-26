"""Priority changes work ordering, not the availability of fallback recognition."""
import threading
from collections import Counter
from types import SimpleNamespace

import numpy as np
import pytest

from lolhex.vision.ocr import OCR, ReadPlan
from lolhex.vision.title_rows import title_groups, title_plan


def box(a, b, c, d):
    return np.array([[a,b],[c,b],[c,d],[a,d]],dtype=float)


def prepared(count=8, scores=None, texts=None):
    o=OCR.__new__(OCR)
    o._lock=threading.Lock()
    o._engine=SimpleNamespace(load_img=lambda x:x, preprocess_img=lambda x:x,
                             detect_and_crop=lambda *a:None, recognize_txt=lambda x:x,text_score=.5)
    boxes=[box(20+i*40,30,50+i*40,50) for i in range(count)]
    crops=[np.full((2,3,3),i,dtype=np.uint8) for i in range(count)]
    calls=[]
    o._regions=lambda image:(boxes,crops,.01)
    def recognize(images):
        ids=[int(image[0,0,0]) for image in images];calls.append(ids)
        return SimpleNamespace(txts=[texts[i] if texts else 'private-'+str(i) for i in ids],
                               scores=[scores[i] if scores else .99 for i in ids],elapse=.01)
    o._recognize_regions=recognize
    return o,calls,boxes


def run(o, groups, accept=lambda lines,prefix:True):
    return o.detect_prioritized(np.zeros((100,400,3),dtype=np.uint8),lambda boxes:groups,accept)


def test_strong_three_titles_skip_body_without_another_detection(caplog):
    o,calls,_=prepared()
    lines,stats=run(o,[ReadPlan((0,1,2),2)])
    assert [x.text for x in lines]==['private-0','private-1','private-2']
    assert calls==[[0,1,2]]
    assert stats=={'detected':8,'recognized':3,'early':True}
    assert 'private-' not in caplog.text


def test_failed_candidates_reuse_reads_and_restore_original_order():
    o,calls,_=prepared()
    lines,stats=run(o,[ReadPlan((0,1,2),2),ReadPlan((0,1,2,3),3)],lambda lines,prefix:False)
    assert [x.text for x in lines]==['private-'+str(i) for i in range(8)]
    assert calls==[[0,1,2],[3],[4,5,6,7]]
    assert Counter(i for group in calls for i in group)==Counter(range(8))
    assert stats=={'detected':8,'recognized':8,'early':False}


@pytest.mark.parametrize('groups',[[],None,[(0,0,1)],[(0,1,999)],[(True,1,2)],[None],[['bad',1,2]],
    [ReadPlan((0,0,1),1)],[ReadPlan((2,3,4),4)],[ReadPlan((0,1,999),1)]])
def test_missing_or_invalid_priority_leaves_normal_recognition(groups):
    o,calls,_=prepared()
    lines,stats=run(o,groups)
    assert len(lines)==8 and calls==[list(range(8))]
    assert not stats['early']


def test_bad_accept_callback_does_not_suppress_remaining_text():
    o,calls,_=prepared()
    def fail(lines,prefix):raise ValueError('optional selector failure')
    lines,stats=run(o,[ReadPlan((0,1,2),2)],fail)
    assert len(lines)==8 and stats['recognized']==8
    assert Counter(i for group in calls for i in group)==Counter(range(8))


def test_low_confidence_or_blank_title_requires_fallback():
    o,calls,_=prepared(5,scores=[.99,.1,.99,.99,.99],texts=['甲甲','乙乙','丙丙','正文',''])
    lines,stats=run(o,[ReadPlan((0,1,2),2)])
    assert [x.text for x in lines]==['甲甲','丙丙','正文']
    assert calls==[[0,1,2],[3,4]] and not stats['early']


def test_empty_detection_performs_no_recognition():
    o,calls,_=prepared(0)
    lines,stats=run(o,[])
    assert lines==[] and calls==[] and stats['recognized']==0


def test_other_engine_adapter_uses_existing_full_call_once():
    o=OCR.__new__(OCR);calls=[]
    o._engine=object();o.detect=lambda image:calls.append(True) or ['same result']
    lines,stats=run(o,[(0,1,2)])
    assert lines==['same result'] and calls==[True] and not stats['early']


REFERENCE=[(0,30,100,60),(120,30,220,60),(240,30,340,60)]


def test_reference_region_priority_excludes_large_body_text():
    boxes=[box(20,35,80,55),box(140,35,200,55),box(260,35,320,55),
           box(10,70,90,110),box(130,70,210,110),box(250,70,330,110)]
    assert title_groups(boxes,REFERENCE)==[(0,1,2)]


@pytest.mark.parametrize('boxes',[
    [box(20,35,80,55),box(140,35,200,55)],
    [box(20,75,80,95),box(140,75,200,95),box(260,75,320,95)],
    [box(20,35,80,55),box(140,35,200,55),box(260,35,float('nan'),55)],
])
def test_unknown_layout_missing_card_or_invalid_box_has_no_priority(boxes):
    assert title_groups(boxes,REFERENCE)==[]


def test_real_detector_only_early_accepts_three_distinct_strong_names():
    from lolhex.vision.detector import AugmentDetector, BAND, DETECT_MAX_W
    from lolhex.vision.matcher import NameMatcher
    from lolhex.vision.ocr import TextLine
    W,H=1920,1080
    x0,y0=int(BAND[0]*W),int(BAND[1]*H)
    scale=min(1,DETECT_MAX_W/(int(BAND[2]*W)-x0))
    names=['甲甲','乙乙','丙丙']
    lines=[TextLine(text,.99,tuple(int(v*scale) for v in (cx-35-x0,430-y0,cx+35-x0,455-y0)))
           for cx,text in zip([590,960,1330],names)]
    accepted=[]
    class Adapter:
        def detect_prioritized(self,image,prioritize,accept):
            raw=[box(*ln.box) for ln in lines]
            assert prioritize(raw)==[ReadPlan((0,1,2),2)]
            accepted.append(accept(lines,lines))
            assert not accept(lines[:2],lines[:2])
            assert not accept([lines[0],lines[1],lines[1]],lines)
            low=[TextLine(names[0],.4,lines[0].box),*lines[1:]]
            assert not accept(low,low)
            return lines,{'detected':9,'recognized':3,'early':True}
    det=AugmentDetector(NameMatcher({str(i):n for i,n in enumerate(names)}),ocr=Adapter())
    offer=det.scan_full(np.zeros((H,W,3),dtype=np.uint8))
    assert accepted==[True] and offer.slots==['0','1','2']
    assert det.cost['full_early']==1 and det.cost['full_recognized']==3


def test_wrong_reference_reads_earlier_true_titles_and_their_neighbours():
    boxes=[box(x,400,x+60,420) for x in (500,900,1300)]
    boxes += [box(x,600,x+60,620) for x in (500,900,1300)]
    boxes += [box(20,800,60,820)]
    plan=title_plan(boxes,(3,4,5),1080,1,(0,0))
    assert plan==ReadPlan(tuple(range(6)),5)


def test_lookahead_anchor_with_unread_fourth_neighbour_forces_full_fallback():
    # The unread B's row only has two columns, but the already-read A has four.
    boxes=[box(x-10,410,x+10,430) for x in (600,960,1320)]
    boxes += [box(590,430,610,450),box(1670,450,1690,470)]
    assert title_plan(boxes,(0,1,2),1080,1,(0,0)) is None


def test_horizontal_upper_bound_uses_maximum_independent_set():
    boxes=[box(x-1,400,x+1,420) for x in (100,500,900)]
    # Score-ordered selection at x=180 could hide the four-point possibility.
    boxes += [box(x-1,700,x+1,720) for x in (180,100,260,500,900)]
    assert title_plan(boxes,(0,1,2),1080,1,(0,0)) is None


def test_prioritized_detector_rejects_three_matches_outside_same_row():
    from lolhex.vision.detector import AugmentDetector
    from lolhex.vision.matcher import NameMatcher
    from lolhex.vision.ocr import TextLine
    names=['甲甲','乙乙','丙丙']
    # Height 80 still passes candidate grouping, but cy span exceeds _pick_row.
    lines=[TextLine(n,.99,(x-30,y-40,x+30,y+40)) for n,x,y in zip(names,(450,810,1170),(312,347,382))]
    class Adapter:
        def detect_prioritized(self,image,prioritize,accept):
            assert not accept(lines,lines)
            return lines,{'detected':3,'recognized':3,'early':False}
    det=AugmentDetector(NameMatcher({str(i):n for i,n in enumerate(names)}),ocr=Adapter())
    assert det.scan_full(np.zeros((1080,1920,3),dtype=np.uint8)) is None


def test_late_partial_row_cannot_establish_prefix_precedence():
    from lolhex.vision.detector import AugmentDetector
    from lolhex.vision.matcher import NameMatcher
    from lolhex.vision.ocr import TextLine
    names=['甲甲','乙乙','丙丙']
    prefix=[TextLine('正文',.99,(100,100,200,120))]
    lines=prefix+[TextLine(n,.99,(x-30,312,x+30,332)) for n,x in zip(names,(360,730,1100))]
    class Adapter:
        def detect_prioritized(self,image,prioritize,accept):
            assert not accept(lines,prefix)
            return lines,{'detected':6,'recognized':4,'early':False}
    det=AugmentDetector(NameMatcher({str(i):n for i,n in enumerate(names)}),ocr=Adapter())
    det.scan_full(np.zeros((1080,1920,3),dtype=np.uint8))


def test_later_plan_keeps_all_previously_read_lines_in_original_order():
    o,calls,_=prepared()
    seen=[]
    def accept(lines,prefix):
        seen.append(([ln.text for ln in lines],[ln.text for ln in prefix]))
        assert all(any(ln is p for ln in lines) for p in prefix)
        return len(seen)==2
    lines,stats=run(o,[ReadPlan((0,1,2,7),2),ReadPlan((0,1,2,3,4),4)],accept)
    assert [ln.text for ln in lines]==['private-'+str(i) for i in (0,1,2,3,4,7)]
    assert seen[1][1]==['private-'+str(i) for i in range(5)]
    assert calls==[[0,1,2,7],[3,4]] and stats['recognized']==6 and stats['early']


def test_wrong_calibration_body_names_cannot_replace_earlier_real_titles():
    from lolhex.vision.detector import AugmentDetector,Calibration
    from lolhex.vision.matcher import NameMatcher
    from lolhex.vision.ocr import TextLine
    names=['甲甲','乙乙','丙丙','丁丁','戊戊','己己','ordinary text']
    o,calls,_=prepared(7,texts=names)
    full=[(x-30,y-12,x+30,y+12) for y in (440,650) for x in (590,960,1330)]
    full.append((50,800,150,820))
    local=[tuple(v-(230 if j%2==0 else 108) for j,v in enumerate(b)) for b in full]
    raw=[box(*b) for b in local]
    o._regions=lambda image:(raw,[np.full((2,3,3),i,dtype=np.uint8) for i in range(7)],.01)
    all_lines=[TextLine(n,.99,b) for n,b in zip(names,local)]
    legacy=SimpleNamespace(detect=lambda image:all_lines)
    offers=[]
    for adapter in (legacy,o):
        det=AugmentDetector(NameMatcher({str(i):n for i,n in enumerate(names[:6])}),ocr=adapter)
        det.calibration=Calibration([(a/1920,b/1080,c/1920,d/1080) for a,b,c,d in
                                    ((450,620,730,680),(820,620,1100,680),(1190,620,1470,680))],(1920,1080))
        offers.append(det.scan_full(np.zeros((1080,1920,3),dtype=np.uint8)))
    assert all(offer.slots==['0','1','2'] for offer in offers)
    assert offers[0].boxes==offers[1].boxes
    assert calls==[list(range(6))] and det.cost['full_early']==1


def test_unvalidated_custom_language_model_keeps_existing_full_call():
    from lolhex.vision.detector import AugmentDetector
    from lolhex.vision.matcher import NameMatcher
    calls=[]
    class Adapter:
        rec_model='custom-korean.onnx'
        def detect(self,image):calls.append('full');return []
        def detect_prioritized(self,*args):raise AssertionError('custom model must keep its existing path')
    assert AugmentDetector(NameMatcher({}),ocr=Adapter()).scan_full(np.zeros((1080,1920,3),np.uint8)) is None
    assert calls==['full']
