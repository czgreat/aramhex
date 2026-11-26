"""主界面样式用的小图标：勾（check）和下拉箭头（chevron），1～3 倍各一张。

    python scripts/make_ui_icons.py

Qt 按屏幕缩放自动挑 @2x、@3x，125%、150% 也不糊。
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPolygonF  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "lolhex" / "ui"
ICONS = {  # 名字: (边长, 线宽, 颜色, 折线)
    "check": (16, 2.0, "#ffffff", ((3.6, 8.4), (6.8, 11.4), (12.4, 5.0))),
    "chevron": (12, 1.6, "#667085", ((2.6, 4.4), (6.0, 7.8), (9.4, 4.4))),
}


def draw(size: int, width: float, color: str, points, scale: int) -> QImage:
    img = QImage(size * scale, size * scale, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(scale, scale)
    p.setPen(QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPolyline(QPolygonF([QPointF(*pt) for pt in points]))
    p.end()
    return img


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    for name, spec in ICONS.items():
        for scale in (1, 2, 3):
            path = OUT / (f"{name}.png" if scale == 1 else f"{name}@{scale}x.png")
            draw(*spec, scale).save(str(path))
            print(path.name, path.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
