"""海克斯三选一画面的识别与低负载跟踪。

怎么判定"现在要选海克斯"：不依赖固定坐标，而是看画面里是否同时出现
≥2 个能匹配到海克斯字典的文字，且它们在同一水平线上、左右排开。

负载控制分三级：
1. 未校准：每 bootstrap_interval 秒截取画面中部，缩小后做一次"检测+识别"（单线程约 1 秒）。
   一旦完整识别到三张，就记住三个标题的位置（按窗口比例保存，换分辨率自动失效）。
2. 已校准：每 scan_interval 只截三个标题所在的一条窄带；先算缩略图指纹，
   和上次一样就直接复用结果（几乎零开销）；变了才对三个小块做"仅识别"（每块约 20ms）。
3. 已校准但识别不到：说明没在选海克斯，保持第 2 级的轻量检查。
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field, replace
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from .. import cloud
from ..config import Settings
from .matcher import NameMatcher
from .ocr import OCR, TextLine
from .window import Rect

log = logging.getLogger(__name__)

# 未校准时扫描的区域（相对窗口）：三张卡片都在屏幕中部。
BAND = (0.12, 0.10, 0.88, 0.85)  # x0, y0, x1, y1
DETECT_MAX_W = 1600               # 检测前把区域缩到这个宽度以内（4K 下约缩一半）
REC_HEIGHT = 48                   # 识别前把标题块缩放到这个高度
# 没有本机校准时先用的默认标题位置：国服 4K（16:9）实机多次校准的结果，按窗口比例。
# 游戏界面按高度缩放、水平居中，其他宽高比按这个规律换算。猜错了也没关系：
# 识别不到就照常整块扫描，扫到三张后换成本机的校准结果。
DEFAULT_BOXES = [(0.2309, 0.3869, 0.3923, 0.4335), (0.4231, 0.3869, 0.5845, 0.4335),
                 (0.6152, 0.3869, 0.7767, 0.4335)]


@dataclass
class Calibration:
    """三个标题框，存为相对窗口的比例坐标 (x0, y0, x1, y1)。guess：默认位置，还没在本机确认过。"""
    boxes: list[tuple[float, float, float, float]]
    size: tuple[int, int]
    guess: bool = False

    @classmethod
    def default(cls, w: int, h: int) -> "Calibration":
        k = (16 / 9) * h / max(1, w)  # 16:9 → 1；更宽的屏幕卡片往中间收
        boxes = [(0.5 + (x0 - 0.5) * k, y0, 0.5 + (x1 - 0.5) * k, y1) for x0, y0, x1, y1 in DEFAULT_BOXES]
        return cls(boxes, (w, h), guess=True)

    def pixel_boxes(self, w: int, h: int) -> list[tuple[int, int, int, int]]:
        return [(int(a * w), int(b * h), int(c * w), int(d * h)) for a, b, c, d in self.boxes]

    def valid(self) -> bool:
        """标题区域须在客户区内、从左到右且互不重叠；不要求固定间距。"""
        if len(self.boxes) != 3 or len(self.size) != 2 or any(not math.isfinite(v) or v <= 0 for v in self.size):
            return False
        for box in self.boxes:
            if len(box) != 4 or not all(math.isfinite(v) for v in box):
                return False
            a,b,c,d = box
            if not (0 <= a < c <= 1 and 0 <= b < d <= 1):
                return False
            if int(c*self.size[0]) <= int(a*self.size[0]) or int(d*self.size[1]) <= int(b*self.size[1]):
                return False
        return all(left[2] <= right[0] for left,right in zip(self.boxes,self.boxes[1:]))


@dataclass
class Offer:
    slots: list[str | None]
    texts: list[str]
    scores: list[float]
    boxes: list[tuple[int, int, int, int] | None]  # 屏幕坐标下的标题框
    at: float = field(default_factory=time.time)
    padded: bool = False  # boxes 是否为带上下留白的校准框；原始 OCR 字框不含留白
    source: str = "full"
    observation: int = 0  # 真实 OCR 批次；指纹缓存返回不增加。
    trace: dict | None = None  # 单调时钟诊断；不是卡片真实出现时刻，不进入 UI 状态。
    group_changed: bool = False  # 整组三选一正在换牌，暂不发布旧卡混合组

    def key(self) -> tuple:
        return tuple(self.slots)

    def matched(self) -> int:
        return sum(1 for s in self.slots if s)


def _resize(img: np.ndarray, scale: float) -> np.ndarray:
    if abs(scale - 1.0) < 1e-3:
        return img
    import cv2
    h, w = img.shape[:2]
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=interp)


def fingerprint(img: np.ndarray) -> np.ndarray:
    import cv2
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return cv2.resize(g, (96, 12), interpolation=cv2.INTER_AREA).astype(np.int16)


def fp_changed(a: np.ndarray | None, b: np.ndarray) -> bool:
    """只换了一张卡时整体均值变化很小，所以看局部：任一格子明显变化就算变了。"""
    if a is None or a.shape != b.shape:
        return True
    d = np.abs(a - b)
    return float(d.max()) > 24 or float(d.mean()) > 3


class AugmentDetector:
    def __init__(self, matcher: NameMatcher, calib_path: Path | None = None, ocr: OCR | None = None,
                 rarities: dict[str, str] | None = None):
        self.matcher = matcher
        self.calib_path = calib_path
        self._ocr = ocr
        self.rarities = rarities or {}
        defaults = Settings()
        self.recheck_seconds = defaults.recheck_seconds
        self.pending_seconds = defaults.pending_seconds
        self.low_match_reads = defaults.low_match_reads
        self.group_confirm = defaults.group_confirm
        self.calibration: Calibration | None = None
        self._last_fp: np.ndarray | None = None
        self._last_result: Offer | None = None
        self._last_window: Rect | None = None
        self._cells: list[dict | None] = [None] * 3
        # 识别线程写回读卡结果，和别的线程清空跟踪状态互斥；OCR 本身不在锁里
        self._track_lock = threading.RLock()
        self._stable: Offer | None = None
        self._pending: list[dict | None] = [None] * 3
        self._low_match_reads = 0  # 已稳定后，有限次数的新 OCR 确认；缓存空值不作新证据。
        self._group_target: list[str | None] | None = None
        self._group_counts: list[int] = [0] * 3
        self._group_since = 0.0
        self._observation = 0
        self._color_logged: set[tuple] = set()  # 记过日志的颜色冲突 (aid, 看到的颜色, 处置方式)，每种只记一次
        # 卡框颜色和资料稀有度对不上时怎么办（引擎按云控 color_check 每轮设）：
        # log 只记日志、照样按名字认（1.1.3 默认）；reject 名字不是一字不差就拒绝（1.1.2 做法加 #36）；off 不看颜色
        self.color_check = "log"
        # 这局花在认字上的次数和时间（秒），对局结束写进日志，玩家说卡的时候看
        # *_cpu 是本线程实际算了多久（单线程推理在调用线程上跑）；比 *_s（墙钟）小很多说明线程在排队等 CPU
        self.cost = {"strip": 0, "strip_s": 0.0, "strip_cpu": 0.0, "strip_ocr": 0,
                     "full": 0, "full_s": 0.0, "full_cpu": 0.0,
                     "full_lines": 0, "full_matches": 0, "strip_cache": 0,
                     "name_reject": 0, "color_reject": 0, "color_trust": 0, "pending": 0}
        self.calibration = self._load_calibration()

    def _load_calibration(self) -> Calibration | None:
        """本机存的三选一位置（calibration.json）。只有当前画面上真认出三张卡才会写这个文件，
        所以它总是本机确认过的位置；没有或坏了返回 None。"""
        if not (self.calib_path and self.calib_path.exists()):
            return None
        try:
            d = json.loads(self.calib_path.read_text(encoding="utf-8"))
            calibration = Calibration([tuple(b) for b in d["boxes"]], tuple(d["size"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not calibration.valid():
            log.warning("忽略无效三选一校准：标题区域越界、重叠或顺序错误")
            return None
        return calibration

    def restore_calibration(self) -> bool:
        """丢掉内存里的位置后，换回本机存的那份（和刚启动时一样）。分辨率不同的那份不会被用上：
        calibrated_for 按尺寸比，对不上照旧先用默认位置再重新定位。"""
        with self._track_lock:
            self.calibration = self._load_calibration()
            return self.calibration is not None

    @property
    def ocr(self) -> OCR:
        if self._ocr is None:
            self._ocr = OCR.get()
        return self._ocr

    def clear_tracking(self) -> None:
        """离开对局时保留定位校准，丢弃上一局标题与待确认读数。"""
        with self._track_lock:
            self._last_fp = self._last_result = self._last_window = None
            self._cells = [None]*3
            self._stable = None
            self._pending = [None]*3
            self._low_match_reads = 0
            self._group_target = None
            self._group_counts = [0] * 3

    def set_matcher(self, matcher: NameMatcher) -> None:
        with self._track_lock:
            self.matcher = matcher
            self._last_fp = None
            self._cells = [None] * 3
            self._low_match_reads = 0
            self._group_target = None
            self._group_counts = [0] * 3

    @property
    def color_check(self) -> str:
        return self._color_check

    @color_check.setter
    def color_check(self, value: str) -> None:
        """换了处置方式：各列缓存的结果是按旧方式判的，下一轮全部重读（和 set_matcher 一样，
        不清 _stable / _last_result，选卡中标签不闪）。"""
        with self._track_lock:
            if getattr(self, "_color_check", value) != value:
                self._last_fp = None
                self._cells = [None] * 3
                self._low_match_reads = 0
            self._color_check = value

    def calibrated_for(self, w: int, h: int) -> bool:
        return self.calibration is not None and tuple(self.calibration.size) == (w, h)

    def confirmed_for(self, w: int, h: int) -> bool:
        return self.calibrated_for(w, h) and not self.calibration.guess

    def ensure_calibration(self, w: int, h: int) -> None:
        """这个窗口大小还没校准过：先用默认位置，第一次选海克斯就能走轻量检查。"""
        if not self.calibrated_for(w, h):
            self.calibration = Calibration.default(w, h)
            self._last_fp = None
            self._cells = [None] * 3
            self._stable = None
            self._pending = [None] * 3
            self._low_match_reads = 0
            self._group_target = None
            self._group_counts = [0] * 3

    def reset_calibration(self) -> None:
        with self._track_lock:
            self.calibration = None
            self._last_fp = None
            self._cells = [None] * 3
            self._stable = None
            self._pending = [None] * 3
            self._low_match_reads = 0
            self._group_target = None
            self._group_counts = [0] * 3
        if self.calib_path and self.calib_path.exists():
            self.calib_path.unlink()

    def _save_calibration(self) -> None:
        if getattr(self, "_defer_calibration", False):
            self._calibration_dirty = True
            return
        self._write_calibration()

    def _write_calibration(self) -> None:
        if self.calib_path and self.calibration:
            self.calib_path.write_text(json.dumps({"boxes": self.calibration.boxes,
                                                   "size": self.calibration.size}), encoding="utf-8")

    @contextmanager
    def defer_calibration_writes(self):
        """扫描结果仍属当前窗口才提交定位；过时帧不能先污染磁盘。"""
        self._defer_calibration, self._calibration_dirty = True, False
        try:
            yield lambda: self._write_calibration() if self._calibration_dirty else None
        finally:
            self._defer_calibration, self._calibration_dirty = False, False

    # ---------- 1. 全区域扫描 ----------

    def scan_full(self, frame: np.ndarray, origin: tuple[int, int] = (0, 0)) -> Offer | None:
        """frame 为整个游戏客户区。找到 ≥2 张卡返回 Offer；3 张齐全时顺便校准。"""
        t0, c0 = time.perf_counter(), time.thread_time()
        try:
            return self._scan_full(frame, origin)
        finally:
            self.cost["full"] += 1
            self.cost["full_s"] += time.perf_counter() - t0
            self.cost["full_cpu"] += time.thread_time() - c0

    def _scan_full(self, frame: np.ndarray, origin: tuple[int, int]) -> Offer | None:
        H, W = frame.shape[:2]
        if not self.calibrated_for(W, H):
            self._stable = None
            self._pending = [None]*3
        x0, y0, x1, y1 = int(BAND[0] * W), int(BAND[1] * H), int(BAND[2] * W), int(BAND[3] * H)
        band = frame[y0:y1, x0:x1]
        scale = min(1.0, DETECT_MAX_W / max(1, band.shape[1]))
        image = _resize(band, scale)
        priority = getattr(self.ocr, "detect_prioritized", None)
        # Split recognition batches are validated with the bundled model only.
        if getattr(self.ocr, "rec_model", None) or not cloud.feature("title_priority"):
            priority = None  # 云控 features.title_priority=false：退回 1.1.1 的整块全部认字
        if callable(priority):
            from .title_rows import title_groups, title_plan
            reference = self.calibration if self.calibrated_for(W, H) else Calibration.default(W, H)
            regions = [tuple((v - (x0 if j % 2 == 0 else y0)) * scale for j, v in enumerate(box))
                       for box in reference.pixel_boxes(W, H)]
            def accept_titles(candidates, prefix):
                mapped, anchors = [], []
                prefix_ids = {id(ln) for ln in prefix}
                for ln in candidates:
                    aid, sc = self.matcher.match(ln.text)
                    if aid:
                        hit = (TextLine(ln.text, ln.score, tuple(int(v/scale) + (x0 if j % 2 == 0 else y0)
                               for j, v in enumerate(ln.box))), aid, sc)
                        mapped.append(hit)
                        if id(ln) in prefix_ids:
                            anchors.append(hit)
                def strong(group):
                    return (len(group) == 3 and len({aid for _, aid, _ in group}) == 3
                            and all(ln.score >= .85 and sc >= 90 for ln, _, sc in group))
                # A complete early anchor must already win three matches. Later
                # partial rows cannot establish detector-order precedence.
                if not strong(self._pick_row(mapped, H, anchors=anchors)):
                    return False
                chosen = self._pick_row(mapped, H)
                if not strong(chosen):
                    return False
                chosen.sort(key=lambda t: t[0].cx)
                return self._calibration_candidate(chosen, W, H).valid()
            def plans(boxes):
                return [plan for group in title_groups(boxes, regions)
                        if (plan := title_plan(boxes, group, H, scale, (x0, y0))) is not None]
            lines, stats = priority(image, plans, accept_titles)
            for key, value in (("full_detected", stats["detected"]), ("full_recognized", stats["recognized"]),
                               ("full_early", int(stats["early"]))):
                self.cost[key] = self.cost.get(key, 0) + value
        else:
            lines = self.ocr.detect(image)
        self.cost["full_lines"] += len(lines)
        hits: list[tuple[TextLine, str, float]] = []
        for ln in lines:
            aid, sc = self.matcher.match(ln.text)
            if aid:
                bx = tuple(int(v / scale) for v in ln.box)
                hits.append((TextLine(ln.text, ln.score, (bx[0] + x0, bx[1] + y0, bx[2] + x0, bx[3] + y0)), aid, sc))
        self.cost["full_matches"] += len(hits)
        group = self._pick_row(hits, H)
        self._log_repeated_names(hits, group, W, H)
        if len(group) < 2:
            return None
        if len({aid for _, aid, _ in group if aid}) < 2:
            self.cost["full_duplicate_reject"] = self.cost.get("full_duplicate_reject", 0) + 1
            now = time.monotonic()
            if now-getattr(self, "_duplicate_rejected_at", float("-inf")) >= 60:
                self._duplicate_rejected_at = now
                log.info("拒绝整块识别：匹配列=%d，但不足两个不同海克斯名", len(group))
            return None
        group.sort(key=lambda t: t[0].cx)
        slots, texts, scores, boxes = self._assign_columns(group, W)
        if len(group) == 3:
            if not self._calibrate(group, W, H):
                return None  # 不发布错误位置，也不污染已确认的校准。
            boxes = self.calibration.pixel_boxes(W, H)
        else:
            # 整块扫描已找到卡片，旧条带的空缓存不能遮掉这次恢复。
            self._last_fp = self._last_result = self._last_window = None
            self._cells = [None] * 3
        self._low_match_reads = 0
        ox, oy = origin
        sboxes = [None if b is None else (b[0] + ox, b[1] + oy, b[2] + ox, b[3] + oy) for b in boxes]
        self._observation += 1
        offer = Offer(slots, texts, scores, sboxes, padded=len(group) == 3,
                      source="full", observation=self._observation)
        offer = self._stabilize(offer, [True]*3)
        log.debug("海克斯OCR source=full observation=%d raw=%r matches=%r scores=%r",
                  offer.observation, texts, slots, scores)
        return offer

    def _log_repeated_names(self, hits, group, W, H) -> None:
        """诊断：选中那一行里同一个海克斯在别的横向位置也匹配到了。只记海克斯名和相对位置，限频 60 秒。"""
        if not group:
            return
        cy = group[0][0].cy
        chosen = {aid: ln.cx for ln, aid, _ in group}
        extra = [(aid, ln.cx, sc) for ln, aid, sc in hits
                 if aid in chosen and abs(ln.cy - cy) < 0.03 * H and abs(ln.cx - chosen[aid]) > 0.08 * H]
        if not extra:
            return
        self.cost["full_repeat_name"] = self.cost.get("full_repeat_name", 0) + 1
        now = time.monotonic()
        if now - getattr(self, "_repeat_logged_at", float("-inf")) >= 60:
            self._repeat_logged_at = now
            log.info("整块识别同名多处：选用=%s 另有=%s @ %dx%d",
                     " ".join(f"{aid}@{ln.cx / W:.3f}" for ln, aid, _ in sorted(group, key=lambda t: t[0].cx)),
                     " ".join(f"{aid}@{cx / W:.3f}/{sc:.0f}" for aid, cx, sc in extra), W, H)

    @staticmethod
    def _pick_row(hits, H, anchors=None):
        """同一水平线上、横向分开的匹配项；一张卡只取分数最高的一条。"""
        best: list = []
        once = cloud.feature("same_name_once")
        for ln, _, _ in (hits if anchors is None else anchors):
            row = [t for t in hits if abs(t[0].cy - ln.cy) < 0.03 * H]
            # 同一张卡上可能匹配到多行（标题 + 描述里提到别的海克斯名），按横向距离去重。
            row.sort(key=lambda t: -t[2])
            uniq: list = []
            # 三选一不会出现两张同名卡：同一个海克斯在别处又匹配到（1.1.1 日志里中框常被定在宽 0.38 处，
            # 读成左卡的名字），只留分数最高的那条，别让它挤掉真正的中卡。
            for t in row:
                if all(abs(t[0].cx - u[0].cx) > 0.08 * H and (not once or t[1] != u[1]) for u in uniq):
                    uniq.append(t)
            if len(uniq) > len(best):
                best = uniq
        return best[:3]

    @staticmethod
    def _assign_columns(group, W):
        """把识别到的卡片放进左/中/右三个位置。只有两张时按左右对称推断缺的是哪张。"""
        slots: list[str | None] = [None, None, None]
        texts = ["", "", ""]
        scores = [0.0, 0.0, 0.0]
        boxes: list = [None, None, None]
        if len(group) == 3:
            idx = [0, 1, 2]
        else:
            a, b = group[0][0].cx, group[1][0].cx
            mid = W / 2
            gap = b - a
            if abs((a + b) / 2 - mid) < 0.25 * gap:
                idx = [0, 2]          # 左右两张，中间缺
            elif abs(a - mid) < 0.25 * gap:
                idx = [1, 2]          # 中、右
            else:
                idx = [0, 1]          # 左、中
        for i, (ln, aid, sc) in zip(idx, group):
            slots[i], texts[i], scores[i], boxes[i] = aid, ln.text, sc, ln.box
        return slots, texts, scores, boxes

    @staticmethod
    def _calibration_candidate(group, W, H):
        centers = [t[0].cx for t in group]
        spacing = (centers[2] - centers[0]) / 2
        th = int(np.median([t[0].h for t in group]))
        cy = float(np.median([t[0].cy for t in group]))
        half_w = 0.42 * spacing
        half_h = 0.9 * th
        boxes = [((c - half_w) / W, (cy - half_h) / H, (c + half_w) / W, (cy + half_h) / H) for c in centers]
        return Calibration(boxes, (W, H))

    def _calibrate(self, group, W, H) -> bool:
        candidate = self._calibration_candidate(group, W, H)
        boxes = candidate.boxes
        if not candidate.valid():
            now = time.monotonic()
            if now-getattr(self, "_geometry_rejected_at", float("-inf")) >= 60:
                self._geometry_rejected_at = now
                log.warning("拒绝三选一校准：标题区域越界、重叠或顺序错误 boxes=%s @ %dx%d", boxes, W, H)
            return False
        self.calibration = candidate
        # 定位框变更后不能用旧条带的近似指纹命中旧坐标。
        self._last_fp = self._last_result = self._last_window = None
        self._cells = [None] * 3
        self._low_match_reads = 0
        self._save_calibration()
        log.info("已校准三选一标题位置：%s @ %dx%d", boxes, W, H)
        return True

    # ---------- 2. 已校准的轻量检查 ----------

    def strip_rect(self, win: Rect) -> Rect | None:
        """三个标题框的外接矩形（屏幕坐标），只截这一条。"""
        if not self.calibrated_for(win.width, win.height):
            return None
        pb = self.calibration.pixel_boxes(win.width, win.height)
        x0, y0 = min(b[0] for b in pb), min(b[1] for b in pb)
        x1, y1 = max(b[2] for b in pb), max(b[3] for b in pb)
        if self.rarities:
            from .cardframe import frame_strip_bounds
            bounds = frame_strip_bounds(pb, win.height)
            if bounds:
                x0, y0, x1, y1 = bounds
                x0, x1 = max(0, x0), min(win.width, x1)
        return Rect(win.left + x0, win.top + y0, x1 - x0, y1 - y0)

    def check_strip(self, strip: np.ndarray, win: Rect) -> Offer | None:
        """strip 为 strip_rect 截图。画面没变化时直接复用上次结果。"""
        fp = fingerprint(strip)
        now = time.monotonic()
        active = self._last_result is not None
        confirming = self._stable is not None and 0 < self._low_match_reads < self.low_match_reads
        due = any(c is None for c in self._cells) or (active and any(
            now-c["at"] >= self.recheck_seconds for c in self._cells))
        if self._last_window == win and not fp_changed(self._last_fp, fp) and not due and not any(self._pending) and not confirming and self._group_target is None:
            self.cost["strip_cache"] += 1
            log.debug("海克斯OCR source=cache observation=%d", self._observation)
            return replace(self._last_result, source="cache") if self._last_result else None
        if self._last_window != win:
            self._cells = [None] * 3
            self._low_match_reads = 0
        self._last_fp = fp
        self._last_window = win
        t0, c0 = time.perf_counter(), time.thread_time()
        try:
            return self._read_strip(strip, win)
        finally:
            self.cost["strip"] += 1
            self.cost["strip_s"] += time.perf_counter() - t0
            self.cost["strip_cpu"] += time.thread_time() - c0

    def _read_strip(self, strip: np.ndarray, win: Rect) -> Offer | None:
        pb = self.calibration.pixel_boxes(win.width, win.height)
        region = self.strip_rect(win)
        sx, sy = region.left-win.left, region.top-win.top
        rarities = [None]*3
        if self.rarities and self.color_check != "off":
            from .cardframe import classify_card_frames
            rarities = classify_card_frames(strip, pb, strip_origin=(sx, sy), window_height=win.height)
        slots: list[str | None] = []
        texts, scores, boxes, fresh = [], [], [], []
        now = time.monotonic()
        crops = [strip[y0-sy:y1-sy, x0-sx:x1-sx] for x0, y0, x1, y1 in pb]
        fingerprints = [fingerprint(crop) for crop in crops]
        # 取快照：别的线程（换窗口、对局结束）随时可能 clear_tracking 把 self._cells 换成空的，
        # 这一轮全程按快照算，读完再写回；被清过就不写回旧读数（Gitea #37）
        cells_ref = self._cells
        cells = list(cells_ref)
        changed = [old is None or fp_changed(old["fp"], fp)
                   for old, fp in zip(cells, fingerprints)]
        confirming = self._stable is not None and 0 < self._low_match_reads < self.low_match_reads
        urgent = any(changed) or any(self._pending) or confirming or self._group_target is not None
        expired = [i for i, old in enumerate(cells) if old is not None
                   and self._last_result is not None and now-old["at"] >= self.recheck_seconds]
        # 定期检查一次只读一列；先响应变化，最迟两倍周期内仍安排复查，防动画饿死其他列。
        maintenance = min(expired, key=lambda i: cells[i]["at"]) if expired else None
        if maintenance is not None and urgent and now-cells[maintenance]["at"] < 2*self.recheck_seconds:
            maintenance = None
        for i, (x0, y0, x1, y1) in enumerate(pb):
            crop, fp = crops[i], fingerprints[i]
            old = cells[i]
            read = (old is None or changed[i] or self._pending[i] is not None or self._group_target is not None or i == maintenance
                    or (confirming and old is not None and old["aid"] is None))
            if read:
                scale = REC_HEIGHT / max(1, crop.shape[0])
                txt, conf = self.ocr.recognize(_resize(crop, scale))
                aid, sc = self.matcher.match(txt)
                if not aid:
                    self.cost["name_reject"] += 1
                if aid and rarities[i] and self.rarities.get(aid) != rarities[i]:
                    # 海克斯名字各不相同，名字已经能确定是哪张；颜色只拿来发现资料标错的稀有度（如 2078 连锁反应），
                    # 和名字读错的情况。1.1.3 起默认只记日志，云控 color_check=reject 退回按颜色拒绝
                    exact = self.matcher.exact(txt) == aid
                    if exact or self.color_check != "reject":
                        self.cost["color_trust"] += 1
                        verdict = "按名字认"
                    else:
                        self.cost["color_reject"] += 1
                        verdict = "拒绝"
                    if (aid, rarities[i], verdict) not in self._color_logged:
                        self._color_logged.add((aid, rarities[i], verdict))
                        log.info("海克斯颜色和资料不一致：aid=%s 读到=%r 分数=%.0f 名字一字不差=%s 资料=%s 看到=%s，%s",
                                 aid, txt, sc, exact, self.rarities.get(aid), rarities[i], verdict)
                    if verdict == "拒绝":
                        aid = None
                cells[i] = {"fp": fp, "at": now, "text": txt, "conf": conf, "aid": aid, "score": sc}
                self.cost["strip_ocr"] += 1
                log.debug("海克斯OCR source=strip column=%d raw=%r confidence=%.3f match=%r score=%.1f",
                          i, txt, conf, aid, sc)
            else:
                txt, aid, sc = old["text"], old["aid"], old["score"]
            fresh.append(read)
            slots.append(aid)
            texts.append(txt)
            scores.append(sc)
            boxes.append((win.left + x0, win.top + y0, win.left + x1, win.top + y1))
        # 检查和写回放在同一把锁里：clear_tracking 也拿这把锁，检查通过后不会再被清空（Gitea #37 复核）
        with self._track_lock:
            if self._cells is not cells_ref:
                return None  # 读的时候被清空了（换窗口、对局结束）：这一轮的结果作废
            self._cells = cells
            if any(fresh):
                self._observation += 1
            offer = Offer(slots, texts, scores, boxes, padded=True, source="strip", observation=self._observation)
            if self.calibration.guess and offer.matched() == 3:
                self.calibration.guess = False  # 默认位置在本机对上了，存下来
                self._save_calibration()
            if offer.matched() >= 2:
                if self._low_match_reads:
                    log.info("三选一跟踪恢复：低命中新观察=%d 本轮重读=%s", self._low_match_reads, fresh)
                self._low_match_reads = 0
                self._last_result = self._stabilize(offer, fresh)
            else:
                self._last_result = None
                for i, read in enumerate(fresh):
                    if read:
                        self._pending[i] = None  # 本轮不可靠读数中断此前的“连续新观察”。
                # 暂不发布旧卡，只保留内部连续状态供短暂失败恢复。引擎仍按3次None关闭。
                # 确认期间绕过空缓存，仅重读失败列，三次None因而来自三个真实OCR批次。
                if self._stable is not None and any(fresh):
                    self._low_match_reads += 1
                    log.info("三选一跟踪待确认：低命中新观察=%d/%d 本轮重读=%s 命中=%d",
                             self._low_match_reads, self.low_match_reads, fresh, offer.matched())
                if self._stable is None or self._low_match_reads >= self.low_match_reads:
                    self._stable = None
                    self._pending = [None] * 3
            return self._last_result

    def _stabilize(self, offer: Offer, fresh: list[bool]) -> Offer:
        """单列只采纳连续的新 OCR 观察，短暂读空不重排整组；不无限保留旧卡。"""
        if self._stable is None:
            self._stable = offer
            return offer
        changed = [i for i, (old, new) in enumerate(zip(self._stable.slots, offer.slots))
                   if fresh[i] and new and old != new]
        now = time.monotonic()
        if self.group_confirm and self._group_target is None and len(changed) >= 2:
            self._group_target = [None] * 3
            self._group_counts = [0] * 3
            self._group_since = now
            log.info("三选一整组换牌待确认：变化列=%s", changed)
        if self._group_target is not None:
            for i, aid in enumerate(offer.slots):
                if not fresh[i] or not aid:
                    continue
                if self._group_target[i] == aid:
                    self._group_counts[i] += 1
                else:
                    self._group_target[i] = aid
                    self._group_counts[i] = 1
            done = [aid if count >= 2 else None for aid, count in zip(self._group_target, self._group_counts)]
            if all(done) or now - self._group_since >= 2 * self.pending_seconds:
                # 超时（如一列是新卡、认不出）只发已确认的列，其余列回到单列确认，不再整组盯着读
                if not all(done):
                    log.info("三选一整组换牌超时：已确认=%s", done)
                accepted = done
                self._group_target = None
                self._group_counts = [0] * 3
                self._stable = replace(offer, slots=accepted)
                return self._stable
            self._stable = replace(offer, slots=[None] * 3, group_changed=True)
            return self._stable
        accepted = list(self._stable.slots)
        for i, aid in enumerate(offer.slots):
            if not fresh[i]:
                continue
            if aid == accepted[i]:
                self._pending[i] = None
                continue
            prior = self._pending[i]
            first = prior["first"] if prior else now
            count = prior["count"]+1 if prior and prior["aid"] == aid else 1
            if count >= 2:
                accepted[i] = aid
                self._pending[i] = None
            else:
                self.cost["pending"] += 1
                self._pending[i] = {"aid": aid, "count": count, "first": first}
                if now-first >= self.pending_seconds:
                    accepted[i] = None
        stable = replace(offer, slots=accepted)
        log.debug("海克斯确认 observation=%d raw_matches=%r published=%r pending=%r",
                  offer.observation, offer.slots, accepted, [p is not None for p in self._pending])
        self._stable = stable
        return stable

    def column_of(self, x: int, y: int, win: Rect) -> int | None:
        """屏幕坐标 (x, y) 落在哪张卡的竖列上；用于记录玩家点了哪张。"""
        if not self.calibrated_for(win.width, win.height):
            return None
        pb = self.calibration.pixel_boxes(win.width, win.height)
        spacing = (pb[2][0] - pb[0][0]) / 2
        title_y = (pb[0][1] + pb[0][3]) / 2 + win.top
        if abs(y - title_y) > 0.35 * win.height:
            return None
        for i, b in enumerate(pb):
            cx = (b[0] + b[2]) / 2 + win.left
            if abs(x - cx) < spacing / 2:
                return i
        return None
