"""RapidOCR（PaddleOCR 模型的 ONNX 版）封装，离线运行。

两种用法：
- detect(img)：检测+识别，找出画面中所有文字行（约 0.3–0.5s，仅在未校准时用）；
- recognize(img)：已知文字区域时只做识别（每块约 10–20ms）。
模型首次使用时加载（约 0.5s），之后常驻。
韩服：识别模型换成韩文的（use_rec_model，见 langpack.py），检测模型不变。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

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


class OCR:
    _inst = None
    _inst_lock = threading.Lock()
    _rec_model: str | None = None  # 换用的识别模型（韩文）；None 为内置

    def __init__(self, rec_model: str | None = None):
        from rapidocr import RapidOCR
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        if rec_model:
            from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
            self._engine = RapidOCR(params={"Rec.lang_type": LangRec.KOREAN, "Rec.model_type": ModelType.MOBILE,
                                            "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_path": rec_model})
        else:
            self._engine = RapidOCR()
        self.rec_model = rec_model
        self._lock = threading.Lock()

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
        with self._lock:
            r = self._engine(img, use_det=True, use_cls=False, use_rec=True)
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
        with self._lock:
            r = self._engine(img, use_det=False, use_cls=False, use_rec=True)
        if r is None or not getattr(r, "txts", None):
            return "", 0.0
        return str(r.txts[0]), float(r.scores[0])
