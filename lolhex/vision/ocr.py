"""RapidOCR（PaddleOCR 模型的 ONNX 版）封装，离线运行。

两种用法：
- detect(img)：检测+识别，找出画面中所有文字行；
- recognize(img)：已知文字区域时只做识别。
模型首次使用时加载，之后常驻；耗时随输入和机器变化，以运行计时为准。
韩服：识别模型换成韩文的（use_rec_model，见 langpack.py），检测模型不变。
只用一个线程：onnxruntime 默认把所有核都拉来算，对局中每次认字都会和游戏抢 CPU，玩家会掉帧、卡顿。
线程策略须同时考虑游戏负载与识别延迟，不能用开发机耗时外推所有用户。
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from types import SimpleNamespace

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class TextLine:
    text: str
    score: float
    box: tuple[int, int, int, int]  # x0, y0, x1, y1（相对输入图）

    @property
    def cx(self) -> float:
        return (self.box[0] + self.box[2]) / 2

    @property
    def cy(self) -> float:
        return (self.box[1] + self.box[3]) / 2

    @property
    def h(self) -> int:
        return self.box[3] - self.box[1]


THREADS = {"EngineConfig.onnxruntime.intra_op_num_threads": 1, "EngineConfig.onnxruntime.inter_op_num_threads": 1}


@dataclass(frozen=True)
class ReadPlan:
    indices: tuple[int, ...]
    prefix_end: int


class OCR:
    _inst = None
    _inst_lock = threading.Lock()
    _rec_model: str | None = None  # 换用的识别模型（韩文）；None 为内置

    def __init__(self, rec_model: str | None = None):
        from rapidocr import RapidOCR
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        params = dict(THREADS)
        if rec_model:
            from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
            params.update({"Rec.lang_type": LangRec.KOREAN, "Rec.model_type": ModelType.MOBILE,
                           "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_path": rec_model})
        self._engine = RapidOCR(params=params)
        self.rec_model = rec_model
        self._lock = threading.Lock()
        versions = []
        for package in ("rapidocr", "onnxruntime"):
            try:
                versions.append(f"{package}={version(package)}")
            except PackageNotFoundError:
                versions.append(f"{package}=unknown")
        sessions = []
        for name in ("text_det", "text_rec"):
            session = getattr(getattr(getattr(self._engine, name, None), "session", None), "session", None)
            if session is not None:
                try:
                    opts = session.get_session_options()
                    sessions.append(f"{name}:threads={opts.intra_op_num_threads}/{opts.inter_op_num_threads},providers={session.get_providers()}")
                except (AttributeError, RuntimeError):
                    pass
        log.info("OCR运行配置：%s 识别模型=%s %s", " ".join(versions),
                 "韩文" if rec_model else "内置", "; ".join(sessions) or "会话信息不可用")
        cfg = getattr(self._engine, "cfg", None)
        if cfg is not None:
            def setting(section, key):
                value = getattr(getattr(cfg, section, None), key, "unknown")
                return getattr(value, "value", value)
            log.info("OCR模型配置：det=%s/%s/%s rec=%s/%s/%s det_limit=%s/%s rec_batch=%s rec_shape=%s",
                     setting("Det", "lang_type"), setting("Det", "ocr_version"), setting("Det", "model_type"),
                     setting("Rec", "lang_type"), setting("Rec", "ocr_version"), setting("Rec", "model_type"),
                     setting("Det", "limit_type"), setting("Det", "limit_side_len"),
                     setting("Rec", "rec_batch_num"), setting("Rec", "rec_img_shape"))

    @classmethod
    def get(cls) -> "OCR":
        with cls._inst_lock:
            if cls._inst is None or cls._inst.rec_model != cls._rec_model:
                cls._inst = cls(cls._rec_model)
            return cls._inst

    @classmethod
    def use_rec_model(cls, path: str | None) -> None:
        """换识别模型（None 换回内置）；下次 get() 时重新加载。"""
        with cls._inst_lock:
            cls._rec_model = str(path) if path else None

    def detect(self, img: np.ndarray) -> list[TextLine]:
        started = time.perf_counter()
        with self._lock:
            acquired = time.perf_counter()
            r = self._engine(img, use_det=True, use_cls=False, use_rec=True)
        finished = time.perf_counter()
        self._profile("整块", img, r, started, acquired, finished)
        lines = []
        if r is None or getattr(r, "boxes", None) is None or getattr(r, "txts", None) is None:
            return lines
        for box, txt, sc in zip(r.boxes, r.txts, r.scores):
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            lines.append(TextLine(str(txt), float(sc), (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))))
        return lines

    def recognize(self, img: np.ndarray) -> tuple[str, float]:
        if img.size == 0:
            return "", 0.0
        started = time.perf_counter()
        with self._lock:
            acquired = time.perf_counter()
            r = self._engine(img, use_det=False, use_cls=False, use_rec=True)
        finished = time.perf_counter()
        self._profile("标题", img, r, started, acquired, finished)
        if r is None or not getattr(r, "txts", None):
            return "", 0.0
        return str(r.txts[0]), float(r.scores[0])

    def _regions(self, img):
        """Reuse RapidOCR 3.9.2's exact preprocessing/crops, but defer recognition."""
        from rapidocr.main import RapidOCRError
        from rapidocr.utils.process_img import map_boxes_to_original
        original = self._engine.load_img(img)
        prepared, record = self._engine.preprocess_img(original)
        try:
            crops, detected = self._engine.detect_and_crop(prepared, record)
        except RapidOCRError:  # Empty detection, as in RapidOCR.run_ocr_steps.
            return [], [], 0.0
        boxes = map_boxes_to_original(np.array(detected.boxes, copy=True), record, *original.shape[:2])
        return list(boxes), crops, detected.elapse

    def _recognize_regions(self, crops):
        from rapidocr.main import RapidOCRError
        try:
            return self._engine.recognize_txt(crops)
        except RapidOCRError:
            return None

    def detect_prioritized(self, img, prioritize, accept):
        """Detect once; try likely titles, otherwise recognize every remaining crop.

        Candidate failure never means absence. Each detected crop is recognized at
        most once, fallback restores original detector order, and the same engine
        lock/thread limits cover both stages. Other OCR callers retain detect().
        """
        required = ("load_img", "preprocess_img", "detect_and_crop", "recognize_txt")
        if not all(callable(getattr(self._engine, name, None)) for name in required):
            lines = self.detect(img)
            return lines, {"detected": len(lines), "recognized": len(lines), "early": False}
        started = time.perf_counter()
        with self._lock:
            acquired = time.perf_counter()
            boxes, crops, det_s = self._regions(img)
            decoded, rec_s = {}, 0.0
            threshold = float(getattr(self._engine, "text_score", .5))

            def read(indices):
                nonlocal rec_s
                pending = [i for i in indices if i not in decoded]
                if pending:
                    result = self._recognize_regions([crops[i] for i in pending])
                    rec_s += float(getattr(result, "elapse", 0.0) or 0.0)
                    texts = getattr(result, "txts", None)
                    scores = getattr(result, "scores", None)
                    for offset, i in enumerate(pending):
                        decoded[i] = None
                        if texts is None or scores is None or offset >= len(texts) or offset >= len(scores):
                            continue
                        txt, score = str(texts[offset]), float(scores[offset])
                        if not txt.strip() or not math.isfinite(score) or score < threshold:
                            continue
                        box = np.asarray(boxes[i])
                        decoded[i] = TextLine(txt, score, (int(box[:, 0].min()), int(box[:, 1].min()),
                                                              int(box[:, 0].max()), int(box[:, 1].max())))
                return [decoded[i] for i in indices if decoded[i] is not None]

            result, early = None, False
            try:
                groups = list(prioritize(boxes))[:4]
            except Exception:
                groups = []  # Optional priority must not suppress normal recognition.
                log.debug("标题候选生成失败，继续完整认字", exc_info=True)
            for plan in groups:
                if not isinstance(plan, ReadPlan):
                    continue
                group = plan.indices
                if (not isinstance(group, tuple) or len(group) < 3
                        or any(type(i) is not int or not 0 <= i < len(crops) for i in group)
                        or len(set(group)) != len(group)
                        or type(plan.prefix_end) is not int or not 0 <= plan.prefix_end < len(crops)
                        or not set(range(plan.prefix_end+1)).issubset(group)):
                    continue
                read(group)
                lines = [decoded[i] for i in sorted(decoded) if decoded[i] is not None]
                prefix = [decoded[i] for i in range(plan.prefix_end+1) if decoded[i] is not None]
                try:
                    accepted = len(lines) >= 3 and accept(lines, prefix)
                except Exception:
                    accepted = False
                    log.debug("标题候选验证失败，继续完整认字", exc_info=True)
                if accepted:
                    result, early = lines, len(decoded) < len(crops)
                    break
            if result is None:
                result = read(list(range(len(crops))))
            stats = {"detected": len(boxes), "recognized": len(decoded), "early": early}
        finished = time.perf_counter()
        self._profile("整块", img, SimpleNamespace(txts=[x.text for x in result], elapse_list=[det_s, None, rec_s]),
                      started, acquired, finished)
        if finished - getattr(self, "_priority_logged_at", -math.inf) >= 60:
            self._priority_logged_at = finished
            log.info("OCR标题优先：检测框=%d 已认字=%d 提前完成=%s", stats["detected"], stats["recognized"], early)
        return result, stats

    def _profile(self, kind, img, r, started, acquired, finished) -> None:
        # RapidOCR 3.9.2 的 elapse_list 顺序为检测、方向分类、识别；缺值时不猜。
        # 不记录识别文字、图像或本地路径，普通模式也能区分模型慢和等待锁。
        if finished - started >= 1.0 and finished - getattr(self, "_profile_logged_at", {}).get(kind, -math.inf) >= 60:
            logged = getattr(self, "_profile_logged_at", {})
            logged[kind] = finished
            self._profile_logged_at = logged
            stages = getattr(r, "elapse_list", None)
            det_ms = rec_ms = "unknown"
            def milliseconds(value):
                try:
                    seconds = float(value)
                    if math.isfinite(seconds) and seconds >= 0:
                        return f"{seconds * 1000:.0f}"
                except (ValueError, TypeError, OverflowError):
                    pass
                return "unknown"
            if isinstance(stages, (list, tuple)) and len(stages) >= 3:
                det_ms, rec_ms = milliseconds(stages[0]), milliseconds(stages[2])
            elif kind == "标题":
                # RapidOCR 3.9.2 rec-only returns TextRecOutput.elapse, not elapse_list.
                rec_ms = milliseconds(getattr(r, "elapse", None))
            texts = getattr(r, "txts", None)
            log.info("OCR%s耗时：输入=%dx%d 总计=%.0f毫秒 等待锁=%.0f毫秒 检测=%s毫秒 认字=%s毫秒 文本行=%d",
                     kind, img.shape[1], img.shape[0], (finished-started)*1000,
                     (acquired-started)*1000, det_ms, rec_ms, len(texts) if texts is not None else 0)
