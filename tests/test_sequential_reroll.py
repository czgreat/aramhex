"""Overlapping single-column rerolls must not be mistaken for a new offer group."""
from dataclasses import replace
from itertools import permutations

import pytest

from test_offer_group_transition import group_detector
from test_reroll_continuity import change_columns


@pytest.mark.parametrize("order", list(permutations(range(3))))
@pytest.mark.parametrize("reread_all", [False, True])
def test_overlapping_rerolls_publish_each_confirmed_column(monkeypatch, order, reread_all):
    det, _, clock, _, _ = group_detector(monkeypatch)
    original = det._stable
    observed = list(original.slots)
    expected = list(observed)
    for step, column in enumerate(order):
        observed[column] = str(column + 3)
        fresh = [reread_all or i == column or (step > 0 and i == order[step - 1])
                 for i in range(3)]
        clock[0] += .36
        result = det._stabilize(replace(original, slots=list(observed)), fresh)
        if step > 0:
            previous = order[step - 1]
            expected[previous] = observed[previous]
        assert not result.group_changed
        assert result.slots == expected
    clock[0] += .36
    result = det._stabilize(replace(original, slots=observed), [True] * 3)
    assert not result.group_changed and result.slots == observed


def test_logged_middle_right_left_sequence_keeps_engine_recommendations(store, tmp_path, monkeypatch):
    # 2026-10-30 test.4: middle changes, then right while middle awaits its second
    # read, then left. The second batch used to clear all three badges.
    from lolhex.config import Settings
    from lolhex.engine import Engine

    det, ocr, clock, rect, strip = group_detector(monkeypatch)
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase="ingame", hero_id="17")
    engine._on_offer(det._stable)
    steps = [
        ((1,), ["戊戊"], ["0", "1", "2"]),
        ((2,), ["戊戊", "己己"], ["0", "4", "2"]),
        ((0,), ["丁丁", "己己"], ["0", "4", "5"]),
        ((), ["丁丁"], ["3", "4", "5"]),
    ]
    for columns, names, expected in steps:
        clock[0] += .36
        change_columns(det, rect, strip, *columns)
        ocr.values = list(names)
        result = det.check_strip(strip, rect)
        engine._on_offer(result)
        snapshot = engine.snapshot()
        assert not result.group_changed
        assert result.slots == expected
        assert snapshot["offer"]["slots"] == expected
        assert [slot["augment_id"] for slot in snapshot["evaluation"]["slots"]] == expected


def test_two_new_columns_still_start_group_when_another_column_is_pending(monkeypatch):
    det, _, clock, _, _ = group_detector(monkeypatch)
    original = det._stable
    det._stabilize(replace(original, slots=["3", "1", "2"]), [True, False, False])
    clock[0] += .36
    new_group = replace(original, slots=["3", "4", "5"])
    assert det._stabilize(new_group, [True] * 3).group_changed
    clock[0] += .36
    result = det._stabilize(new_group, [True] * 3)
    assert not result.group_changed and result.slots == ["3", "4", "5"]
