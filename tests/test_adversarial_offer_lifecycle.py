"""Exercise confirmation history across whole offer lifecycles, not one reroll."""
from dataclasses import replace

import pytest

from lolhex.vision.matcher import NameMatcher
from test_offer_group_transition import group_detector
from test_reroll_continuity import change_columns


@pytest.mark.parametrize('column', range(3))
@pytest.mark.parametrize('timeout', [False, True])
def test_completed_group_does_not_reuse_previous_single_column_confirmation(monkeypatch, column, timeout):
    det, _, clock, _, _ = group_detector(monkeypatch)
    initial = det._stable
    old_pending = list(initial.slots)
    old_pending[column] = 'old-candidate'
    fresh = [i == column for i in range(3)]
    det._stabilize(replace(initial, slots=old_pending), fresh)
    new_group = ['new-left', 'new-middle', 'new-right']
    clock[0] += .3
    assert det._stabilize(replace(initial, slots=new_group), [True] * 3).group_changed
    clock[0] += 2 * det.pending_seconds + .01 if timeout else .3
    expected = list(new_group)
    if timeout:
        expected[(column + 1) % 3] = None  # This column never gets its second observation.
    assert det._stabilize(replace(initial, slots=expected), [True] * 3).slots == expected
    later = list(expected)
    later[column] = 'old-candidate'  # One observation; the other read was before the entire new group.
    clock[0] += .3
    result = det._stabilize(replace(initial, slots=later), fresh)
    assert result.slots == expected, 'an ancient single read was counted as the current second confirmation'


def test_actual_strip_group_completion_then_old_name_misread_requires_confirmation(monkeypatch, store, tmp_path):
    from lolhex.config import Settings
    from lolhex.engine import Engine

    det, ocr, clock, rect, strip = group_detector(monkeypatch)
    det.matcher = NameMatcher({str(i): name for i, name in enumerate(
        ['甲甲', '乙乙', '丙丙', '丁丁', '戊戊', '己己', '庚庚'])})

    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase='ingame', hero_id='17')
    engine._on_offer(det._stable)

    def read(columns, names, pixel):
        clock[0] += .31
        change_columns(det, rect, strip, *columns, value=pixel)
        ocr.values = list(names)
        offer = det.check_strip(strip, rect)
        engine._on_offer(offer)
        return offer

    assert read((0,), ['丁丁'], 200).slots == ['0', '1', '2']
    assert read((0, 1, 2), ['庚庚', '戊戊', '己己'], 160).group_changed
    assert read((), ['庚庚', '戊戊', '己己'], 160).slots == ['6', '4', '5']
    assert read((0,), ['丁丁'], 120).slots == ['6', '4', '5']
    snapshot = engine.snapshot()
    assert snapshot['offer']['slots'] == ['6', '4', '5']
    assert [s['augment_id'] for s in snapshot['evaluation']['slots']] == ['6', '4', '5']
    assert read((), ['丁丁'], 120).slots == ['3', '4', '5']


def test_group_deadline_does_not_reset_after_every_changed_candidate(monkeypatch):
    det, _, clock, _, _ = group_detector(monkeypatch)
    old = det._stable
    assert det._stabilize(replace(old, slots=['3', '4', None]), [True] * 3).group_changed
    for index in range(1, 5):
        clock[0] += .5
        result = det._stabilize(replace(old, slots=['3', f'candidate-{index}', None]), [True] * 3)
    assert not result.group_changed and result.slots == ['3', 'candidate-4', None]


@pytest.mark.parametrize('column', range(3))
def test_name_seen_during_two_blank_columns_is_not_stranded_in_cache(monkeypatch, column):
    det, ocr, clock, rect, strip = group_detector(monkeypatch)
    scene = ['甲甲', '乙乙', '丙丙']
    after = ['0', '1', '2']
    after[column] = '5'
    transient = [''] * 3
    transient[column] = '己己'
    change_columns(det, rect, strip, 0, 1, 2, value=200)
    ocr.values = transient
    assert det.check_strip(strip, rect) is None
    clock[0] += .31
    scene[column] = '己己'
    for i in range(3):
        if i != column:
            change_columns(det, rect, strip, i, value=120)
    # OCR provides names based on each physical column, regardless of which columns
    # the scheduler decides to reread; the unchanged candidate must be confirmed.
    names = {200: '己己', **{120 + i: name for i, name in enumerate(scene) if i != column}}
    for i in range(3):
        if i != column:
            change_columns(det, rect, strip, i, value=120 + i)
    ocr.recognize = lambda image: (names[int(round(float(image.mean())))], 1.0)
    det.check_strip(strip, rect)
    clock[0] += .31
    result = det.check_strip(strip, rect)
    assert result.slots == after, 'cached new name never entered confirmation after two other columns failed'


def test_vision_loop_keeps_labels_during_blank_then_confirms_the_cached_new_name(monkeypatch, store, tmp_path):
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.vision import capture, window

    det, ocr, clock, rect, strip = group_detector(monkeypatch)
    names = ['甲甲', '乙乙', '丙丙', '丁丁', '戊戊', '己己']
    ocr.recognize = lambda image: (
        '' if int(image.mean()) == 248 else names[(int(image.mean()) - 32) // 36], 1.0)
    scenes = [[0, 1, 2], [None, None, 5], [0, 1, 5], [0, 1, 5], [0, 1, 5]]
    snapshots = []
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase='ingame', hero_id='17')
    engine._detector = det
    engine._save_offer_shot = lambda rect: None
    engine._sync_augment_tables = lambda detector: None

    def scene(index):
        for column, aid in enumerate(scenes[index]):
            change_columns(det, rect, strip, column, value=248 if aid is None else 32 + 36 * aid)

    class Stop:
        index = 0
        def is_set(self):
            return self.index >= len(scenes)
        def wait(self, delay):
            snapshots.append(engine.snapshot())
            self.index += 1
            clock[0] += .31
            if not self.is_set():
                scene(self.index)

    engine._stop = Stop()
    monkeypatch.setattr(window, 'find_game_window', lambda: 1)
    monkeypatch.setattr(window, 'client_rect', lambda hwnd: rect)
    monkeypatch.setattr(window, 'is_foreground', lambda hwnd: True)
    monkeypatch.setattr(window, 'lower_thread_priority', lambda: None)
    monkeypatch.setattr(capture, 'grab', lambda area: strip)
    scene(0)
    engine._vision_loop()
    assert snapshots[1]['offer']['slots'] == ['0', '1', '2'], 'one blank batch must not close visible labels'
    assert snapshots[3]['offer']['slots'] == ['0', '1', '5']
    assert [slot['augment_id'] for slot in snapshots[3]['evaluation']['slots']] == ['0', '1', '5']
    assert det.cost['full'] == 0


@pytest.mark.parametrize('seed', range(8))
def test_generated_scene_sequences_converge_and_keep_ui_scores_paired(monkeypatch, store, tmp_path, seed):
    """Pixel changes drive real OCR scheduling; expected final names come from the scene."""
    import random
    import numpy as np
    from PySide6.QtWidgets import QApplication
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui import overlay

    rng = random.Random(seed)
    names = ['甲甲', '乙乙', '丙丙', '丁丁', '戊戊', '己己']
    det, _, clock, rect, strip = group_detector(monkeypatch)

    class SceneOCR:
        def recognize(self, image):
            value = int(round(float(np.mean(image))))
            return ('' if value == 248 else names[(value - 32) // 36]), 1.0

    det._ocr = SceneOCR()
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase='ingame', hero_id='17')
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(overlay.win, 'IS_WINDOWS', False)
    layer = overlay.BadgeLayer()
    layer._scale = lambda: 1.0
    history = []

    def observe(scene):
        clock[0] += rng.choice([.11, .31, .71])
        for column, aid in enumerate(scene):
            change_columns(det, rect, strip, column, value=248 if aid is None else 32 + 36 * aid)
        offer = det.check_strip(strip, rect)
        history.append({'scene': list(scene), 'time': clock[0],
                        'published': offer.slots if offer else None,
                        'source': offer.source if offer else None,
                        'group': offer.group_changed if offer else None,
                        'pending': [dict(p) if p else None for p in det._pending],
                        'target': list(det._group_target) if det._group_target else None,
                        'counts': list(det._group_counts)})
        engine._on_offer(offer)
        snap = engine.snapshot()
        if snap['offer']:
            assert snap['offer']['slots'] == [slot['augment_id'] for slot in snap['evaluation']['slots']]
        layer.update_from(snap, rect)
        assert layer.isVisible() == bool(snap['offer'] and snap['evaluation'])
        return offer

    try:
        for step in range(50):
            scene = rng.sample(range(6), 3)
            # Short animations and partial failures can overlap previous confirmations.
            for _ in range(rng.randrange(4)):
                transient = list(scene)
                for column in rng.sample(range(3), rng.randrange(4)):
                    transient[column] = None if rng.randrange(2) else rng.randrange(6)
                observe(transient)
            if step % 11 == 0:
                det.clear_tracking()
                engine._on_offer(None)
            if step % 13 == 0:
                det.set_matcher(NameMatcher({str(i): name for i, name in enumerate(names)}))
            for _ in range(4):
                final = observe(scene)
            assert final is not None and not final.group_changed
            if final.slots != list(map(str, scene)):
                import json
                (tmp_path / 'failed-trace.json').write_text(json.dumps(history, indent=2), encoding='utf-8')
            assert final.slots == list(map(str, scene)), (seed, step, scene, history[-12:])
    finally:
        layer.close()
        app.processEvents()
