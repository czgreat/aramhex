"""The priority guard must use the final integer geometry and strict row rules."""

import pytest

from lolhex.vision.ocr import ReadPlan
from lolhex.vision.title_rows import title_plan


def quad(x0, y0, x1, y1):
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


@pytest.mark.parametrize(
    ("y0", "y1", "included"),
    [(319.99, 340.99, True), (320.01, 340.01, False), (320.99, 341.99, False)],
    ids=["inside-59px", "equal-60px", "outside-61px"],
)
def test_row_neighbour_uses_strict_distance_after_integer_mapping(y0, y1, included):
    # H=2000 gives an exact 60px radius. The inside float box would appear
    # outside if compared before OCR's truncation and coordinate restoration.
    boxes = [quad(x - 50, 290, x + 50, 310) for x in (200, 400, 600)]
    # Share the first column, so adding this neighbour cannot create four columns.
    boxes.append(quad(150, y0, 250, y1))

    plan = title_plan(boxes, (0, 1, 2), 2000, .5, (230, 108))

    expected = (0, 1, 2, 3) if included else (0, 1, 2)
    assert plan == ReadPlan(expected, 2)


@pytest.mark.parametrize(
    ("y0", "y1", "included"),
    [(195.99, 215.99, True), (196.01, 215.01, False)],
    ids=["inside-64px", "outside-65px"],
)
def test_4k_row_neighbour_survives_both_rounding_boundaries(y0, y1, included):
    # A 3840px window has a 2919px BAND, capped to 1600px. After both integer
    # conversions these gaps are 64/65px around 0.03*2160=64.8; using float
    # centres instead would classify both cases the opposite way.
    boxes = [quad(x - 50, 160.01, x + 50, 180.01) for x in (200, 400, 600)]
    boxes.append(quad(150, y0, 250, y1))

    plan = title_plan(boxes, (0, 1, 2), 2160, 1600 / 2919, (460, 216))

    expected = (0, 1, 2, 3) if included else (0, 1, 2)
    assert plan == ReadPlan(expected, 2)


@pytest.mark.parametrize(
    ("x0", "x1", "four_columns"),
    [(259.99, 300.99, False), (260.99, 300.99, False), (260.01, 301.01, True)],
    ids=["inside-159px", "equal-160px", "outside-161px"],
)
def test_horizontal_independence_uses_strict_distance_after_integer_mapping(x0, x1, four_columns):
    # H=2000 makes the minimum independent spacing exactly 160px. Only the
    # >160px case can make a later row outrank the complete three-column prefix.
    boxes = [quad(x - 50, 290, x + 50, 310) for x in (200, 400, 600)]
    boxes += [
        quad(180.01, 600, 220.01, 620),
        quad(x0, 600, x1, 620),
        quad(430, 600, 470, 620),
        quad(630, 600, 670, 620),
    ]

    plan = title_plan(boxes, (0, 1, 2), 2000, .5, (230, 108))

    assert plan == (None if four_columns else ReadPlan((0, 1, 2), 2))


@pytest.mark.parametrize(
    ("first_fraction", "x0", "x1", "four_columns"),
    [(.01, 276.99, 313.99, False), (.99, 275.01, 315.01, True)],
    ids=["inside-172.5px", "outside-173px"],
)
def test_4k_independent_columns_survive_both_rounding_boundaries(first_fraction, x0, x1, four_columns):
    # The actual separations are 172.5/173px around 0.08*2160=172.8. A guard
    # using unrounded centres would reject the first plan and allow the unsafe
    # second one, whose later row can contain four independent matches.
    boxes = [quad(x - 50, 160, x + 50, 180) for x in (200, 400, 600)]
    boxes += [
        quad(180 + first_fraction, 600, 220 + first_fraction, 620),
        quad(x0, 600, x1, 620),
        quad(430, 600, 470, 620),
        quad(630, 600, 670, 620),
    ]

    plan = title_plan(boxes, (0, 1, 2), 2160, 1600 / 2919, (460, 216))

    assert plan == (None if four_columns else ReadPlan((0, 1, 2), 2))
