"""三选一标签放卡片下方时，先看描述文字写到哪：放不下就退回标题上方，不挡海克斯的内容。

只看截图像素：标题中心往下、卡片中间一列里，文字最低出现在哪一行。文字按笔画边缘认（左右相邻像素
亮度差得多）：描述里有灰色的小字，只按亮度认会漏掉；卡片底色再亮也是平滑过渡，没有这种边缘。
游戏界面按高度缩放，所以都按「1080P 像素」（游戏画面高度 / 1080）计。
"""

from __future__ import annotations

import numpy as np

SCAN_FROM = 20     # 标题中心往下多少开始看（跳过标题本身）
SCAN_TO = 228      # 看到哪（标签底边在 236；再往下到 248 是卡片内框，两角的花纹会伸进中间这一列）
HALF_WIDTH = 120   # 卡片中间这么宽一列（卡片内宽约 270，避开两边的边框）
EDGE = 25          # 左右相邻像素亮度差超过这个算笔画边缘
MIN_EDGES = 6      # 一行里至少这么多处边缘才算有字（一个汉字一行就有好几处）；连续两行（1080P 像素）以上才算一行字，
                   # 卡片边上一闪一闪的小星星只有几个像素、一两行高，不算


def desc_end(frame: np.ndarray, box, origin: tuple[int, int] = (0, 0)) -> float | None:
    """box 是屏幕坐标的标题框，frame 是以 origin 为左上角截的游戏画面。
    返回描述文字最低一行在标题中心下方多少（1080P 像素）；这一列里没有字返回 SCAN_FROM；看不了返回 None。"""
    if box is None or frame is None or frame.ndim != 3:
        return None
    h, w = frame.shape[:2]
    u = h / 1080
    cx = (box[0] + box[2]) / 2 - origin[0]
    cy = (box[1] + box[3]) / 2 - origin[1]
    x0, x1 = int(max(0, cx - HALF_WIDTH * u)), int(min(w, cx + HALF_WIDTH * u))
    y0, y1 = int(max(0, cy + SCAN_FROM * u)), int(min(h, cy + SCAN_TO * u))
    if x1 - x0 < 10 or y1 - y0 < 10:
        return None
    lum = frame[y0:y1, x0:x1].max(axis=2).astype(np.int16)
    rows = (np.abs(np.diff(lum, axis=1)) > EDGE).sum(axis=1) >= MIN_EDGES
    need = max(2, round(2 * u))
    last, run = None, 0
    for i, on in enumerate(rows):
        run = run + 1 if on else 0
        if run >= need:
            last = i
    return float(SCAN_FROM) if last is None else (y0 + last - cy) / u
