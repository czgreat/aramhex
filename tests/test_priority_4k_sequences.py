"""4K state sequences across real priority/full/strip code, with scripted OCR.

These tests exercise coordinates, caching and publication decisions. They do not
measure inference speed, capture the desktop, or estimate live game FPS.
"""

import json
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from lolhex.engine import Engine
from lolhex.vision import capture, window
from lolhex.vision.detector import AugmentDetector, BAND, Calibration, DETECT_MAX_W
from lolhex.vision.matcher import NameMatcher
from lolhex.vision.ocr import OCR


WIDTH, HEIGHT = 3840, 2160
CENTERS = (1180, 1920, 2660)
WORDS = ("甲甲", "乙乙", "丙丙", "丁丁", "戊戊", "己己")
MARKERS = (30, 80, 130)


def make_detector(monkeypatch, *, origin=(0, 0), path=None, words=None, centers=CENTERS):
    import lolhex.vision.detector as module

    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    rect = window.Rect(*origin, WIDTH, HEIGHT)
    x0, y0 = int(BAND[0] * WIDTH), int(BAND[1] * HEIGHT)
    scale = DETECT_MAX_W / (int(BAND[2] * WIDTH) - x0)
    words = list(words or WORDS[:3]) + ["ordinary body"] * 9
    bounds = [(x - 90, 865, x + 90, 905) for x in centers]
    bounds += [(x - 90, y, x + 90, y + 40) for y in (1200, 1350, 1500) for x in CENTERS]
    raw = []
    for a, b, c, d in bounds:
        a, c = (a - x0) * scale, (c - x0) * scale
        b, d = (b - y0) * scale, (d - y0) * scale
        raw.append(np.array(((a, b), (c, b), (c, d), (a, d)), dtype=float))
    crops = [np.full((2, 3, 3), i, np.uint8) for i in range(len(raw))]
    ocr = OCR.__new__(OCR)
    ocr._lock = threading.Lock()
    ocr.rec_model = None
    ocr._engine = SimpleNamespace(load_img=lambda value: value, preprocess_img=lambda value: value,
                                 detect_and_crop=lambda *args: None, recognize_txt=lambda value: value,
                                 text_score=.5)
    ocr.full_reads, ocr.strip_reads = [], []
    ocr.on_detect = None
    ocr.title_outputs = {marker: (word, .99) for marker, word in zip(MARKERS, WORDS)}

    def regions(image):
        if ocr.on_detect is not None:
            ocr.on_detect()
        return raw, crops, .01

    def recognize_regions(images):
        indices = [int(image[0, 0, 0]) for image in images]
        ocr.full_reads.append(indices)
        return SimpleNamespace(txts=[words[i] for i in indices], scores=[.99] * len(indices), elapse=.01)

    def recognize_title(image):
        marker = int(image[0, 0, 0])
        ocr.strip_reads.append(marker)
        return ocr.title_outputs[marker]

    ocr._regions, ocr._recognize_regions, ocr.recognize = regions, recognize_regions, recognize_title
    detector = AugmentDetector(NameMatcher({str(i): word for i, word in enumerate(WORDS)}),
                               calib_path=path, ocr=ocr)
    frame = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
    return detector, ocr, clock, rect, frame


def paint_column(detector, rect, strip, column, marker):
    x0, y0, x1, y1 = detector.calibration.pixel_boxes(WIDTH, HEIGHT)[column]
    region = detector.strip_rect(rect)
    strip[y0 - (region.top - rect.top):y1 - (region.top - rect.top),
          x0 - (region.left - rect.left):x1 - (region.left - rect.left)] = marker


def establish_strip(detector, ocr, rect, frame):
    full = detector.scan_full(frame, (rect.left, rect.top))
    assert full.slots == ["0", "1", "2"]
    assert detector.cost["full_early"] == 1
    assert ocr.full_reads == [[0, 1, 2]]
    region = detector.strip_rect(rect)
    strip = np.zeros((region.height, region.width, 3), np.uint8)
    for column, marker in enumerate(MARKERS):
        paint_column(detector, rect, strip, column, marker)
    result = detector.check_strip(strip, rect)
    assert result.slots == full.slots and result.boxes == full.boxes
    assert ocr.strip_reads == list(MARKERS)
    return strip, full


@pytest.mark.parametrize("column", [0, 1, 2])
@pytest.mark.parametrize("origin", [(0, 0), (-3840, 120)])
def test_4k_priority_to_single_column_reroll_keeps_other_columns_and_positions(monkeypatch, column, origin):
    detector, ocr, clock, rect, frame = make_detector(monkeypatch, origin=origin)
    strip, full = establish_strip(detector, ocr, rect, frame)
    calibration = detector.calibration
    # All columns are due for maintenance; the real change still takes priority.
    clock[0] += detector.RECHECK_SECONDS + .1
    marker = 180 + 20 * column
    ocr.title_outputs[marker] = WORDS[column + 3], .99
    paint_column(detector, rect, strip, column, marker)

    first = detector.check_strip(strip, rect)
    assert first.slots == ["0", "1", "2"] and first.boxes == full.boxes
    assert ocr.strip_reads == [*MARKERS, marker]
    clock[0] += .31
    second = detector.check_strip(strip, rect)
    expected = ["0", "1", "2"]
    expected[column] = str(column + 3)
    assert second.slots == expected and second.boxes == full.boxes
    assert ocr.strip_reads == [*MARKERS, marker, marker]
    assert detector.calibration is calibration and detector.cost["full"] == 1

    # Finish the postponed one-column maintenance, then verify the stable cache.
    for other in range(3):
        if other != column:
            clock[0] += .31
            assert detector.check_strip(strip, rect).slots == expected
    reads = list(ocr.strip_reads)
    observation = detector._observation
    cached = detector.check_strip(strip, rect)
    assert cached.source == "cache" and cached.slots == expected
    assert ocr.strip_reads == reads and cached.observation == observation


@pytest.mark.parametrize("failed", [(0, 1), (0, 2), (1, 2)])
def test_4k_two_failed_columns_retry_identical_pixels_without_restarting_all_three(monkeypatch, failed):
    detector, ocr, clock, rect, frame = make_detector(monkeypatch, origin=(-3840, 120))
    strip, full = establish_strip(detector, ocr, rect, frame)
    stable = detector._stable
    for column in failed:
        marker = 225 + 10 * column
        paint_column(detector, rect, strip, column, marker)
        ocr.title_outputs[marker] = "", .99
    clock[0] += .31

    assert detector.check_strip(strip, rect) is None
    assert detector._stable is stable and detector._low_match_reads == 1
    failed_markers = [225 + 10 * column for column in failed]
    assert ocr.strip_reads == [*MARKERS, *failed_markers]
    # No pixel changes: fresh retries, rather than a cached negative, recover.
    for column in failed:
        ocr.title_outputs[225 + 10 * column] = WORDS[column], .99
    clock[0] += .31
    recovered = detector.check_strip(strip, rect)
    assert recovered.slots == ["0", "1", "2"] and recovered.boxes == full.boxes
    assert detector._low_match_reads == 0 and detector.cost["full"] == 1
    assert ocr.strip_reads == [*MARKERS, *failed_markers, *failed_markers]


def test_4k_priority_scan_during_window_move_cannot_save_or_publish_old_positions(monkeypatch, tmp_path):
    path = tmp_path / "calibration.json"
    original = Calibration.default(WIDTH, HEIGHT)
    path.write_text(json.dumps({"boxes": original.boxes, "size": original.size}), encoding="utf-8")
    before = path.read_bytes()
    detector, ocr, clock, old_rect, frame = make_detector(monkeypatch, path=path)
    current = {"rect": old_rect}
    engine = Engine.__new__(Engine)
    engine._lock = threading.RLock()
    engine._scan_epoch = 0
    engine._detector = detector
    engine.state = {"phase": "ingame", "vision": {}, "offer": None, "evaluation": None}
    engine._reevaluate = lambda: {"ok": True}
    monkeypatch.setattr(window, "find_game_window", lambda: 7)
    monkeypatch.setattr(window, "client_rect", lambda hwnd: current["rect"])
    monkeypatch.setattr(window, "is_foreground", lambda hwnd: True)
    captures = []
    monkeypatch.setattr(capture, "grab", lambda rect: captures.append(rect) or frame)
    new_rect = window.Rect(-3840, 120, WIDTH, HEIGHT)
    ocr.on_detect = lambda: current.update(rect=new_rect)

    assert engine._observed_scan(detector, old_rect, full=True, reason="test", hwnd=7) is None
    assert engine._scan_discarded and detector.calibration is None
    assert engine.state["offer"] is None and path.read_bytes() == before

    ocr.on_detect = None
    recovered = engine._observed_scan(detector, new_rect, full=True, reason="test", hwnd=7)
    engine._on_offer(recovered)
    assert recovered.slots == ["0", "1", "2"]
    assert engine.state["offer"]["boxes"] == recovered.boxes
    assert captures == [old_rect, new_rect]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["size"] == [WIDTH, HEIGHT] and detector.calibration.valid()
    for box, expected_x in zip(recovered.boxes, CENTERS):
        assert abs((box[0] + box[2]) / 2 - (new_rect.left + expected_x)) < 5
        assert new_rect.top < box[1] < box[3] < new_rect.bottom


@pytest.mark.parametrize("words", [(WORDS[0], "ordinary body", WORDS[0]), (WORDS[0],) * 3])
def test_4k_full_rejects_one_dictionary_identity_at_multiple_positions(monkeypatch, tmp_path, words):
    path = tmp_path / "calibration.json"
    good = Calibration.default(WIDTH, HEIGHT)
    path.write_text(json.dumps({"boxes": good.boxes, "size": good.size}), encoding="utf-8")
    before = path.read_bytes()
    detector, ocr, clock, rect, frame = make_detector(monkeypatch, path=path, words=words)
    original = detector.calibration

    assert detector.scan_full(frame) is None
    assert detector.calibration is original and path.read_bytes() == before
    assert detector.cost["full_recognized"] == 12 and detector.cost["full_early"] == 0
    assert sorted(i for call in ocr.full_reads for i in call) == list(range(12))


def test_4k_full_still_recovers_two_distinct_cards_when_one_title_is_unknown(monkeypatch):
    detector, ocr, clock, rect, frame = make_detector(monkeypatch, words=(WORDS[0], "ordinary body", WORDS[2]))

    offer = detector.scan_full(frame, (rect.left, rect.top))

    assert offer.slots == ["0", None, "2"] and not offer.padded
    assert detector.cost["full_recognized"] == 12 and detector.cost["full_early"] == 0


@pytest.mark.parametrize("order", ["real_first", "ghost_first"])
def test_4k_full_repeated_left_name_at_038_never_saves_shifted_middle(monkeypatch, tmp_path, order):
    # 1.1.1 线上日志：坏校准的中框中心几乎都在宽 0.38 处，且读成左卡的名字。
    ghost = int(0.38 * WIDTH)
    if order == "real_first":
        centers, words = (*CENTERS, ghost), (WORDS[0], WORDS[1], WORDS[2], WORDS[0])
    else:
        centers, words = (ghost, *CENTERS), (WORDS[0], WORDS[0], WORDS[1], WORDS[2])
    path = tmp_path / "calibration.json"
    detector, ocr, clock, rect, frame = make_detector(monkeypatch, path=path, words=words, centers=centers)

    offer = detector.scan_full(frame, (rect.left, rect.top))

    assert detector.cost["full_repeat_name"] == 1
    if offer is not None:
        assert len(set(s for s in offer.slots if s)) == len([s for s in offer.slots if s])
    if detector.calibration is not None and path.exists():
        boxes = detector.calibration.pixel_boxes(WIDTH, HEIGHT)
        for box, expected_x in zip(boxes, CENTERS):
            assert abs((box[0] + box[2]) / 2 - expected_x) < 5
    if order == "real_first":
        assert offer.slots == ["0", "1", "2"] and offer.padded
