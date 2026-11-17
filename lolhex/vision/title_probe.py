"""只检查标题像素是否值得认字，不确定海克斯身份，也不判断稀有度。"""

from __future__ import annotations

from enum import Enum

import numpy as np


class TitleSignal(Enum):
    POSSIBLE = "possible"
    EMPTY = "empty"
    UNKNOWN = "unknown"


def probe_titles(image: np.ndarray, boxes, origin: tuple[int, int]) -> TitleSignal:
    """检查已有截图，不再截图、缩放或调用模型。

    任一标题区域存在少量亮色字形像素即放行。所有区域都缺少这种信号
    才返回 EMPTY；这不是“没有卡片”的确定结论，调用方必须定期认字兜底。
    坐标不完整、被裁切或输入格式不支持时返回 UNKNOWN，继续原识别。
    """
    if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
            or image.ndim != 3 or image.shape[2] != 3
            or not isinstance(boxes, (tuple, list)) or len(boxes) not in (3, 4)):
        return TitleSignal.UNKNOWN
    height, width = image.shape[:2]
    ox, oy = origin
    crops = []
    for box in boxes:
        if (not isinstance(box, (tuple, list)) or len(box) != 4
                or any(type(v) is not int for v in box)):
            return TitleSignal.UNKNOWN
        x0, y0, x1, y1 = box
        x0, x1, y0, y1 = x0 - ox, x1 - ox, y0 - oy, y1 - oy
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            return TitleSignal.UNKNOWN
        if x1 - x0 < 24 or y1 - y0 < 8:
            return TitleSignal.UNKNOWN
        crops.append(image[y0:y1, x0:x1])
    for crop in crops:
        low, high = crop.min(axis=2), crop.max(axis=2)
        # 留出淡入、抗锯齿和暖色标题的余量；不把彩色边框当作身份依据。
        ink = (low >= 80) & (high >= 110) & (high - low <= 150)
        if np.count_nonzero(ink) >= 4 and np.count_nonzero(ink.any(axis=1)) >= 2:
            return TitleSignal.POSSIBLE
    return TitleSignal.EMPTY
