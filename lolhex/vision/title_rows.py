"""Priority within existing title regions; uncertainty always leaves full OCR intact."""
from itertools import product
import math
from .ocr import ReadPlan


def title_groups(boxes, reference):
    """At most four triples, one detected text center in each reference title box.

    This only orders work. It neither asserts card presence nor rejects unfamiliar
    layouts. Calibration only orders work; it cannot prove that these are titles.
    """
    columns = [[], [], []]
    for i, raw in enumerate(boxes):
        points = list(raw)
        if not points or not all(math.isfinite(float(v)) for p in points for v in p):
            continue
        a, b = min(p[0] for p in points), min(p[1] for p in points)
        c, d = max(p[0] for p in points), max(p[1] for p in points)
        if c <= a or d <= b:
            continue
        cx, cy = (a+c)/2, (b+d)/2
        for col, (x0, y0, x1, y1) in enumerate(reference):
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                # Prefer centered, similarly sized candidates within the reference.
                distance = abs(cy-(y0+y1)/2)/max(1, y1-y0)
                columns[col].append((distance, -(d-b), i, cy, d-b))
    if not all(columns):
        return []
    choices = []
    for triple in product(*(sorted(col)[:3] for col in columns)):
        heights = [v[4] for v in triple]
        if max(heights) > 2*min(heights) or max(v[3] for v in triple)-min(v[3] for v in triple) > max(heights):
            continue
        indices = tuple(v[2] for v in triple)
        if len(set(indices)) == 3:
            choices.append((sum(v[0] for v in triple), indices))
    return [indices for _, indices in sorted(choices)[:4]]


def title_plan(boxes, candidate, height, scale, offset):
    """Read a complete detector-order prefix and every possible row neighbour.

    A later row must not supersede the prefix under the existing _pick_row rule.
    Its maximum independent horizontal count must therefore be at most three.
    Coordinates follow TextLine's two integer conversions exactly.
    """
    mapped = []
    for raw in boxes:
        if not all(math.isfinite(float(v)) for point in raw for v in point):
            return None
        bounds = (min(p[0] for p in raw), min(p[1] for p in raw),
                  max(p[0] for p in raw), max(p[1] for p in raw))
        b = tuple(int(int(v)/scale)+offset[j % 2] for j, v in enumerate(bounds))
        mapped.append(((b[0]+b[2])/2, (b[1]+b[3])/2))
    end = max(candidate)
    radius, separation = .03*height, .08*height
    neighbours = [[j for j, (_, y) in enumerate(mapped) if abs(y-cy) < radius]
                  for _, cy in mapped]
    needed = set(range(end+1))
    for row in neighbours[:end+1]:
        needed.update(row)
    # Include lookahead anchors in this guard: their other neighbours may be unread.
    for row in neighbours[end+1:]:
        xs = sorted(mapped[j][0] for j in row)
        last, count = -math.inf, 0
        for x in xs:
            if x-last > separation:
                last, count = x, count+1
        if count >= 4:
            return None
    return ReadPlan(tuple(sorted(needed)), end)
