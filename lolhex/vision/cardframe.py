"""保守的卡框像素证据原型；不截图、不认字，也不据此直接确定海克斯。

输入为已有标题条的 BGR 像素、三个带留白的标题框及其屏幕坐标。
调用者需要按 frame_strip_bounds 提供条带：只增加左右约 0.02 个窗口高度，
不增加竖向范围。尚无真实棱彩边框样本，所以棱彩及不确定颜色均返回 None。
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

Box = tuple[int, int, int, int]


def _geometry(boxes: Sequence[Box | None], window_height: int):
    if window_height <= 0 or len(boxes) != 3 or any(b is None for b in boxes):
        return None
    if any(b[2] <= b[0] or b[3] <= b[1] for b in boxes):
        return None
    centers = [(b[0] + b[2]) / 2 for b in boxes]
    gaps = np.diff(centers)
    if gaps[0] <= 0 or abs(gaps[0] - gaps[1]) > 0.03 * window_height:
        return None
    spacing = float(np.mean(gaps))
    if not 0.20 * window_height <= spacing <= 0.50 * window_height:
        return None
    return centers, spacing, max(3, round(0.02 * window_height))


def frame_strip_bounds(boxes: Sequence[Box | None], window_height: int) -> Box | None:
    """所需像素矩形（与 boxes 相同坐标系）；无法推定三列布局时返回 None。"""
    geometry = _geometry(boxes, window_height)
    if geometry is None:
        return None
    centers, spacing, radius = geometry
    return (round(centers[0] - 0.42 * spacing) - radius,
            min(b[1] for b in boxes),
            round(centers[2] + 0.42 * spacing) + radius + 1,
            max(b[3] for b in boxes))


def _side(strip, xs, ys, inward, window_height):
    # 固定最多 12 行、每两列取样；4K 下每一侧也只检查约五百个像素。
    points = strip[ys[:, None], xs[None, :], :3].astype(np.int16)
    b, g, r = points[..., 0], points[..., 1], points[..., 2]
    low, high = points.min(axis=2), points.max(axis=2)
    silver = (low >= 96) & (high <= 210) & (high - low <= 12)
    gold = (r >= 130) & (b >= 65) & (r - g >= 18) & (g - b >= 14) & (r - b >= 40)
    # 竖边内侧还必须是卡片的暗底；场景里的灰色/金色物体不是卡框证据。
    inside_x = xs + inward * max(4, round(0.018 * window_height))
    if inside_x.min() < 0 or inside_x.max() >= strip.shape[1]:
        return None
    dark = strip[ys[:, None], inside_x[None, :], :3].max(axis=2) <= 65
    stable = (points.max(axis=0) - points.min(axis=0)).max(axis=1) <= 28
    labels = []
    for name, mask in (("silver", silver), ("gold", gold)):
        good = (mask.mean(axis=0) >= 0.90) & (dark.mean(axis=0) >= 0.90) & stable
        # 不接受孤立亮点，需要至少两个相邻取样列组成竖轨。
        if np.any(good[:-1] & good[1:]):
            labels.append(name)
    return labels[0] if len(labels) == 1 else None


def classify_card_frames(strip: np.ndarray, boxes: Sequence[Box | None], *,
                         strip_origin: tuple[int, int], window_height: int) -> list[str | None]:
    """返回每列的 silver/gold/None；只提供额外证据，None 不等于卡片不存在。

    要求双侧稳定竖边一致，且标题区域存在亮色字形像素。卡内文字仍在淡入、
    缺少任一侧像素、背景干扰、棱彩未验证颜色均不猜。不能替代 OCR 稳定性判断。
    """
    geometry = _geometry(boxes, window_height)
    result = [None, None, None]
    if geometry is None or strip.ndim != 3 or strip.shape[2] < 3 or strip.dtype != np.uint8:
        return result
    centers, spacing, radius = geometry
    ox, oy = strip_origin
    for i, (cx, box) in enumerate(zip(centers, boxes)):
        x0, y0, x1, y1 = box
        # 略过上侧留白，避免浮层标签被当成标题；仅做亮点存在性检查。
        ty = np.linspace(y0 + (y1-y0)*0.25, y0 + (y1-y0)*0.78, 10).astype(int) - oy
        tx = np.linspace(x0 + (x1-x0)*0.08, x1 - (x1-x0)*0.08, 96).astype(int) - ox
        if min(tx) < 0 or max(tx) >= strip.shape[1] or min(ty) < 0 or max(ty) >= strip.shape[0]:
            continue
        title = strip[ty[:, None], tx[None, :], :3]
        lit = title.min(axis=2) >= 170
        if lit.sum() < 8 or np.count_nonzero(lit.any(axis=1)) < 3:
            continue
        ys = np.linspace(y0 + (y1-y0)*0.25, y1-1, 12).astype(int) - oy
        if min(ys) < 0 or max(ys) >= strip.shape[0]:
            continue
        sides = []
        for sign in (-1, 1):
            edge = round(cx + sign * 0.42 * spacing) - ox
            xs = np.arange(edge-radius, edge+radius+1, 2)
            if xs.min() < 0 or xs.max() >= strip.shape[1]:
                sides.append(None)
            else:
                sides.append(_side(strip, xs, ys, -sign, window_height))
        if sides[0] is not None and sides[0] == sides[1]:
            result[i] = sides[0]
    return result
