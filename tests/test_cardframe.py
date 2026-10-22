"""卡框像素原型的保守拒绝边界；不依赖 OCR 或截图接口。"""

import numpy as np
import pytest

from lolhex.vision.cardframe import classify_card_frames, frame_strip_bounds


def _frame(height=1080, rarity="silver", origin=(-640, 120)):
    scale = height / 1080
    spacing = 369 * scale
    centers = [origin[0] + 960*scale + (i-1)*spacing for i in range(3)]
    boxes = [(round(c-spacing*.42), round(origin[1]+430*scale),
              round(c+spacing*.42), round(origin[1]+480*scale)) for c in centers]
    bounds = frame_strip_bounds(boxes, height)
    ox, oy, x1, y1 = bounds
    image = np.full((y1-oy, x1-ox, 3), 12, np.uint8)
    color = {"silver": (125, 126, 127), "gold": (114, 140, 173),
             "prismatic": (220, 140, 190)}[rarity]
    for c, box in zip(centers, boxes):
        for sign in (-1, 1):
            x = round(c + sign * spacing*.42)-ox
            image[:, x-round(4*scale):x+round(4*scale)+1] = color
        y0, y1 = round((box[3]-box[1])*.38), round((box[3]-box[1])*.65)
        x = round(c)-ox
        image[y0:y1, x-round(50*scale):x+round(50*scale)] = 210
    return image, boxes, bounds


@pytest.mark.parametrize("height", [768, 1080, 1440, 2160])
@pytest.mark.parametrize("rarity", ["silver", "gold"])
def test_two_sides_and_title_support_rarity(height, rarity):
    image, boxes, bounds = _frame(height, rarity)
    assert classify_card_frames(image, boxes, strip_origin=bounds[:2], window_height=height) == [rarity]*3
    assert bounds[1] == min(b[1] for b in boxes)
    assert bounds[3] == max(b[3] for b in boxes)
    assert min(b[0] for b in boxes)-bounds[0] <= round(.02*height)+1
    assert bounds[2]-max(b[2] for b in boxes) <= round(.02*height)+2


@pytest.mark.parametrize("kind", ["empty", "faded", "no_title", "unverified_prismatic", "bright_field"])
def test_uncertain_or_disappeared_frame_is_not_guessed(kind):
    image, boxes, bounds = _frame(rarity="prismatic" if kind == "unverified_prismatic" else "silver")
    if kind == "empty":
        image[:] = 12
    elif kind == "faded":
        image = (image.astype(float)*.5).astype(np.uint8)
    elif kind == "no_title":
        image[(image.min(axis=2) >= 170)] = 12
    elif kind == "bright_field":
        image[:] = 125
    assert classify_card_frames(image, boxes, strip_origin=bounds[:2], window_height=1080) == [None]*3


def test_partial_crop_does_not_guess_last_card():
    image, boxes, bounds = _frame()
    assert classify_card_frames(image[:, :-2], boxes, strip_origin=bounds[:2], window_height=1080) == ["silver", "silver", None]


def test_inconsistent_border_colors_are_rejected():
    image, boxes, bounds = _frame()
    spacing = (boxes[2][0]-boxes[0][0])/2
    for box in boxes:
        cx = (box[0]+box[2])/2
        x = round(cx+.42*spacing)-bounds[0]
        image[:, x-4:x+5] = (114, 140, 173)
    assert classify_card_frames(image, boxes, strip_origin=bounds[:2], window_height=1080) == [None]*3


def test_missing_or_irregular_layout_is_rejected():
    image, boxes, bounds = _frame()
    assert frame_strip_bounds([boxes[0], None, boxes[2]], 1080) is None
    moved = [boxes[0], boxes[1], tuple(v+200 if i%2 == 0 else v for i,v in enumerate(boxes[2]))]
    assert frame_strip_bounds(moved, 1080) is None
    assert classify_card_frames(image, boxes, strip_origin=bounds[:2], window_height=0) == [None]*3
