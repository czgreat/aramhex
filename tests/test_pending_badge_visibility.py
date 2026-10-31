"""Observe what Qt can draw between OCR confirmation and score publication."""
from dataclasses import replace

import pytest

from lolhex.config import Settings
from lolhex.engine import Engine
from lolhex.vision.matcher import NameMatcher
from test_offer_group_transition import group_detector
from test_reroll_continuity import change_columns

IDS = ['2128', '1029', '1373', '2096', '2001', '2002']
NAMES = ['甲甲', '乙乙', '丙丙', '丁丁', '戊戊', '己己']


@pytest.fixture
def scene(monkeypatch, store, tmp_path):
    from PySide6.QtWidgets import QApplication
    from lolhex.ui import overlay

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(overlay.win, 'IS_WINDOWS', False)
    det, ocr, clock, rect, strip = group_detector(monkeypatch)
    det.clear_tracking()
    det.set_matcher(NameMatcher(dict(zip(IDS, NAMES))))
    ocr.values = NAMES[:3]
    initial = det.check_strip(strip, rect)
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase='ingame', hero_id='17')
    engine._on_offer(initial)
    layer = overlay.BadgeLayer()
    layer._scale = lambda: 1.0
    yield det, ocr, clock, rect, strip, engine, layer
    layer.close()
    app.processEvents()


def visible(layer, engine, rect):
    snap = engine.snapshot()
    layer.update_from(snap, rect)
    return {r['index']: (r, label, lines, hint) for
            r, label, _, lines, _, hint, *_ in layer._badge_layout(snap)} if snap['offer'] else {}


@pytest.mark.parametrize('column', range(3))
@pytest.mark.parametrize('source', ['strip', 'full'])
def test_pending_column_hides_old_score_and_item_hint_only(scene, column, source):
    det, ocr, clock, rect, strip, engine, layer = scene
    before = visible(layer, engine, rect)
    old = det._stable
    next_ids = list(old.slots)
    next_ids[column] = IDS[3]
    if source == 'strip':
        change_columns(det, rect, strip, column)
        ocr.values = [NAMES[3]]
        first = det.check_strip(strip, rect)
    else:
        # Full-scan results go through the same stabilizer, without strip scheduling.
        first = det._stabilize(replace(old, slots=next_ids, source='full'), [True] * 3)
    assert first.slots == IDS[:3]  # Two-read confirmation remains in place.
    engine._on_offer(first)
    during = visible(layer, engine, rect)
    assert set(during) == set(range(3)) - {column}
    for i in during:
        assert during[i] == before[i]
    clock[0] += .31
    if source == 'strip':
        ocr.values = [NAMES[3]]
        final = det.check_strip(strip, rect)
    else:
        final = det._stabilize(replace(old, slots=next_ids, source='full'), [True] * 3)
    engine._on_offer(final)
    after = visible(layer, engine, rect)
    assert set(after) == {0, 1, 2}
    assert after[column][0]['augment_id'] == IDS[3]
    assert not any(engine.snapshot()['offer']['pending_columns'])


@pytest.mark.parametrize('column', range(3))
def test_new_name_then_blank_does_not_resurrect_old_badge(scene, column):
    det, ocr, clock, rect, strip, engine, layer = scene
    old = det._stable
    for aid in [IDS[3], None, IDS[3], IDS[3]]:
        clock[0] += .31
        slots = list(old.slots)
        slots[column] = aid
        result = det._stabilize(replace(old, slots=slots), [i == column for i in range(3)])
        engine._on_offer(result)
        drawn = visible(layer, engine, rect)
        if result.slots[column] != IDS[3]:
            assert column not in drawn, 'a blank observation exposed the already-stale score again'
    assert drawn[column][0]['augment_id'] == IDS[3]


@pytest.mark.parametrize('column', range(3))
def test_one_name_misread_then_original_name_restores_without_score_change(scene, column):
    det, _, clock, rect, _, engine, layer = scene
    old = det._stable
    slots = list(old.slots)
    slots[column] = IDS[3]
    fresh = [i == column for i in range(3)]
    engine._on_offer(det._stabilize(replace(old, slots=slots), fresh))
    assert column not in visible(layer, engine, rect)
    clock[0] += .31
    engine._on_offer(det._stabilize(old, fresh))
    assert visible(layer, engine, rect)[column][0]['augment_id'] == old.slots[column]


@pytest.mark.parametrize('column', range(3))
def test_low_match_new_name_masks_that_column_before_batch_recovery(scene, monkeypatch, column):
    from lolhex.vision import capture

    det, ocr, clock, rect, strip, engine, layer = scene
    engine._detector = det
    monkeypatch.setattr(capture, 'grab', lambda area: strip)
    change_columns(det, rect, strip, 0, 1, 2)
    ocr.values = [''] * 3
    ocr.values[column] = NAMES[3]
    assert engine._observed_scan(det, rect, full=False, reason='test') is None
    assert column not in visible(layer, engine, rect)
    assert engine.snapshot()['offer']['slots'] == IDS[:3]
    # Repeated blank observations must not unmask the name observed above.
    clock[0] += .31
    ocr.values = [''] * 3
    assert engine._observed_scan(det, rect, full=False, reason='test') is None
    assert column not in visible(layer, engine, rect)


@pytest.mark.parametrize('column', range(3))
def test_scoring_delay_hides_confirmed_new_column_until_matching_score(scene, monkeypatch, column):
    import lolhex.engine as module

    det, _, _, rect, _, engine, layer = scene
    original = module.recommend.evaluate
    slots = list(det._stable.slots)
    slots[column] = IDS[3]
    seen = []

    def score(*args):
        seen.append(visible(layer, engine, rect))  # UI timer running inside the scoring interval.
        return original(*args)

    monkeypatch.setattr(module.recommend, 'evaluate', score)
    engine._on_offer(replace(det._stable, slots=slots))
    assert set(seen[0]) == {0, 1, 2} - {column}
    assert visible(layer, engine, rect)[column][0]['augment_id'] == IDS[3]


@pytest.mark.parametrize('reset', ['close', 'end', 'tracking', 'group'])
def test_pending_mask_does_not_leak_to_next_offer(scene, reset):
    det, _, _, rect, _, engine, layer = scene
    old = det._stable
    pending = det._stabilize(replace(old, slots=[IDS[3], *old.slots[1:]]), [True, False, False])
    engine._on_offer(pending)
    assert 0 not in visible(layer, engine, rect)
    if reset == 'group':
        engine._on_offer(det._stabilize(replace(old, slots=IDS[3:]), [True] * 3))
    elif reset == 'end':
        engine._on_game_end()
    else:
        engine._on_offer(None)
    det.clear_tracking()
    engine._set(phase='ingame', hero_id='17')
    engine._on_offer(det._stabilize(old, [True] * 3))
    assert set(visible(layer, engine, rect)) == {0, 1, 2}


def test_background_recompute_cannot_unmask_a_new_pending_observation(scene, monkeypatch):
    import lolhex.engine as module

    det, _, _, rect, _, engine, layer = scene
    original = module.recommend.evaluate

    def score(*args):
        engine._mask_offer_columns((False, True, False))
        return original(*args)

    monkeypatch.setattr(module.recommend, 'evaluate', score)
    assert engine._reevaluate() is None
    assert set(visible(layer, engine, rect)) == {0, 2}


def test_main_window_also_suppresses_old_name_score_and_advice(scene):
    from types import SimpleNamespace
    from lolhex.ui.mainwindow import MainWindow

    det, _, _, _, _, engine, _ = scene
    window = MainWindow(engine, SimpleNamespace(set_visible=lambda *args: None))
    try:
        window.show()
        window.refresh()
        old_texts = [lab.text() for lab in window.offer_cards]
        engine._on_offer(det._stabilize(replace(det._stable, slots=[IDS[3], IDS[1], IDS[2]]),
                                       [True, False, False]))
        window.refresh()
        assert window.offer_cards[0].text() == "<span style='color:#777'>识别中…</span>"
        assert [lab.text() for lab in window.offer_cards[1:]] == old_texts[1:]
        assert '等待识别确认' in window.offer_advice.text()
    finally:
        window.hide()
        window.deleteLater()


def test_pending_removal_repaints_old_badge_region_without_other_cards(scene, monkeypatch):
    det, _, _, rect, _, engine, layer = scene
    visible(layer, engine, rect)
    old_region = layer._painted[1][1][1]
    updates = []
    monkeypatch.setattr(layer, 'update', lambda *args: updates.append(args))
    old = det._stable
    engine._on_offer(det._stabilize(replace(old, slots=[IDS[0], IDS[3], IDS[2]]), [False, True, False]))
    visible(layer, engine, rect)
    assert len(updates) == 1 and len(updates[0]) == 1
    dirty = updates[0][0]
    assert dirty.intersects(old_region)
    assert all(not dirty.intersects(layer._painted[1][i][1]) for i in (0, 2))
