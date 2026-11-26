"""打包后的离线自检：在读配置、联网、启动工作线程之前，跑一遍 Qt 窗口、OpenCV 和文字识别。

安装包精简时删掉了一些用不到的库文件；这里实际调用一遍识别要用的功能，删多了就会失败，build.ps1 据此拒绝生成安装包。
"""
from __future__ import annotations

import json
import traceback
from pathlib import Path


def _vision_check() -> dict:
    """OpenCV 的图片处理和 RapidOCR 的检测、识别：画一行字，再认出来。"""
    import cv2
    import numpy as np

    img = np.full((96, 480, 3), 255, np.uint8)
    cv2.putText(img, "AramHex 2026", (12, 64), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 3)
    ok, buf = cv2.imencode(".png", img)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    img = cv2.resize(img, None, fx=1.0, fy=1.0, interpolation=cv2.INTER_AREA)
    from .vision.ocr import OCR
    ocr = OCR.get()
    text, score = ocr.recognize(img)
    lines = ocr.detect(img)
    return {"ocr_text": text, "ocr_score": round(score, 3), "ocr_lines": [ln.text for ln in lines],
            "ocr_ok": bool(ok) and "2026" in text and any("2026" in ln.text for ln in lines)}


def _theme_check(app) -> bool:
    """界面样式能用上，小图标都打进包了。"""
    from PySide6.QtGui import QPixmap
    from .ui import theme
    theme.apply(app)
    return all(not QPixmap(theme.asset(f"{n}{x}.png")).isNull() for n in ("check", "chevron") for x in ("", "@2x", "@3x"))


def run(report_path: str) -> int:
    report = {"success": False}
    try:
        from PySide6.QtCore import qVersion
        from PySide6.QtGui import QPixmap
        from PySide6.QtNetwork import QNetworkAccessManager
        from PySide6.QtWidgets import QApplication, QWidget
        from . import __version__, official
        app = QApplication([])
        widget = QWidget()
        widget.resize(32, 32)
        widget.ensurePolished()
        pixmap = QPixmap(widget.size())
        widget.render(pixmap)
        network = QNetworkAccessManager()
        app.processEvents()
        report.update(version=__version__, official=official.available(), qt=qVersion(), platform=app.platformName(),
                      network_class=network.metaObject().className(), gui_ok=not pixmap.isNull())
        widget.close()
        report.update(_vision_check())
        report["theme_ok"] = _theme_check(app)
        report["success"] = report["gui_ok"] and report["ocr_ok"] and report["theme_ok"]
        app.quit()
    except Exception:
        report["error"] = traceback.format_exc()
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["success"] else 1
