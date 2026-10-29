"""New groups must never be scored together with confirmed cards from the previous group."""
from dataclasses import replace

import pytest

from lolhex.vision.matcher import NameMatcher
from test_ocr_stability import setup_detector
from test_reroll_continuity import change_columns


def group_detector(monkeypatch):
    det, ocr, clock, rect, strip = setup_detector(monkeypatch)
    det.matcher = NameMatcher({str(i): n for i, n in enumerate(["甲甲", "乙乙", "丙丙", "丁丁", "戊戊", "己己"])})
    return det, ocr, clock, rect, strip


def test_new_group_with_late_middle_never_publishes_old_middle(monkeypatch):
    det, ocr, clock, rect, strip = group_detector(monkeypatch)
    change_columns(det, rect, strip, 0, 1, 2)
    ocr.values = ["", "", ""]
    assert det.check_strip(strip, rect) is None  # Animation alone retains the ordinary grace.
    clock[0] += .3
    ocr.values = ["丁丁", "", "己己"]
    first = det.check_strip(strip, rect)
    assert first.slots == [None, None, None] and first.group_changed
    clock[0] += .3
    ocr.values = ["丁丁", "", "己己"]
    assert det.check_strip(strip, rect).slots == [None, None, None]
    change_columns(det, rect, strip, 1, value=180)
    clock[0] += .3
    ocr.values = ["丁丁", "戊戊", "己己"]
    assert det.check_strip(strip, rect).slots == [None, None, None]
    clock[0] += .3
    ocr.values = ["丁丁", "戊戊", "己己"]
    assert det.check_strip(strip, rect).slots == ["3", "4", "5"]


def test_group_change_requires_fresh_read_of_cached_third_column(monkeypatch):
    det, ocr, clock, rect, strip = group_detector(monkeypatch)
    change_columns(det, rect, strip, 0, 2)
    ocr.values = ["丁丁", "己己"]
    assert det.check_strip(strip, rect).slots == [None, None, None]
    clock[0] += .3
    ocr.values = ["丁丁", "戊戊", "己己"]
    assert det.check_strip(strip, rect).slots == [None, None, None]
    clock[0] += .3
    ocr.values = ["丁丁", "戊戊", "己己"]
    assert det.check_strip(strip, rect).slots == ["3", "4", "5"]


@pytest.mark.parametrize("source", ["strip", "full"])
def test_whole_group_requires_two_observations_and_can_reuse_same_card(monkeypatch, source):
    det, _, _, _, _ = group_detector(monkeypatch)
    original = det._stable
    new = replace(original, slots=["3", "1", "5"], source=source)
    assert det._stabilize(new, [True] * 3).slots == [None] * 3
    assert det._stabilize(new, [True] * 3).slots == ["3", "1", "5"]


@pytest.mark.parametrize("column", [0, 1, 2])
def test_single_reroll_keeps_two_other_cards_and_ordinary_confirmation(monkeypatch, column):
    det, _, _, _, _ = group_detector(monkeypatch)
    old = det._stable
    slots = list(old.slots)
    slots[column] = "3"
    fresh = [i == column for i in range(3)]
    first = det._stabilize(replace(old, slots=slots), fresh)
    assert first.slots == old.slots and not first.group_changed
    assert det._stabilize(replace(old, slots=slots), fresh).slots == slots


def run(det, ocr, clock, rect, strip, values, n):
    out = []
    for _ in range(n):
        clock[0] += .3
        before = ocr.calls
        ocr.values = list(values)
        r = det.check_strip(strip, rect)
        out.append((r.slots if r else None, ocr.calls - before))
    return out


def test_whole_group_publishes_on_second_read_without_waiting_recheck(monkeypatch):
    det, ocr, clock, rect, strip = group_detector(monkeypatch)
    change_columns(det, rect, strip, 0, 1, 2)
    out = run(det, ocr, clock, rect, strip, ["丁丁", "戊戊", "己己"], 4)
    assert out[0][0] == [None] * 3 and out[1][0] == ["3", "4", "5"]


def test_unrecognized_column_times_out_without_old_cards(monkeypatch):
    det, ocr, clock, rect, strip = group_detector(monkeypatch)
    change_columns(det, rect, strip, 0, 1, 2)
    out = run(det, ocr, clock, rect, strip, ["丁丁", "完全不认识的新卡", "己己"], 12)
    assert all(s is None or s[1] is None for s, _ in out)  # 旧的中间卡一次都没出现
    assert out[-1] == (["3", None, "5"], 0)  # 超时后发两张，且不再每轮 OCR


def test_group_confirm_off_keeps_per_column_confirmation(monkeypatch):
    det, _, _, _, _ = group_detector(monkeypatch)
    det.group_confirm = False
    new = replace(det._stable, slots=["3", "4", "5"])
    first = det._stabilize(new, [True] * 3)
    assert not first.group_changed and det._group_target is None
