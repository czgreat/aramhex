"""游戏内悬浮层（PySide6）。

- 小按钮：常驻游戏窗口右上角，点一下展开/收起详情（英雄、推荐出装、加点、识别状态）。
  点完按钮、候选或链接后把焦点还给游戏，免得空格等按键落在浮窗上；展开后鼠标离开
  AUTO_HIDE_SECONDS 秒自动收起（正在搜索框里打字时不收）。
- 卡片标签：识别到三选一时，在每张卡的标题下方显示「首选 / 可选 / 可考虑刷新」与依据。
  标签层对鼠标完全透明，不挡游戏点击；所有悬浮窗都设置为不被截图，避免识别到自己。
只有无边框/窗口化模式下悬浮层才能盖在游戏上面。
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

from ..recommend import NODATA_NOTE
from ..vision import window as win
from .. import patch
from . import buildview, single_instance
from .autoshot import AutoShot

from .data_status import data_status_html
from .herosearch import hero_completer

log = logging.getLogger(__name__)

RARITY_CN = {"silver": "白银", "gold": "黄金", "prismatic": "棱彩"}
VERDICT = {"best": ("首选", QColor(95, 211, 141)), "ok": ("可选", QColor(98, 168, 255)),
           "reroll": ("可考虑刷新", QColor(255, 122, 107)), "unknown": ("未知", QColor(150, 150, 150)),
           "nodata": ("无数据", QColor(150, 150, 150))}


AUTO_HIDE_SECONDS = 5


def _dpr() -> float:
    s = QGuiApplication.primaryScreen()
    return s.devicePixelRatio() if s else 1.0


def _logical(x: float) -> int:
    return int(round(x / _dpr()))


def _flags(widget: QWidget, click_through: bool) -> None:
    f = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
    if click_through:
        f |= Qt.WindowTransparentForInput
    widget.setWindowFlags(f)
    widget.setAttribute(Qt.WA_TranslucentBackground)
    widget.setAttribute(Qt.WA_ShowWithoutActivating)


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _unit(height: int) -> float:
    """游戏画面里 1 个「1080P 像素」等于几个屏幕物理像素：标签按这个缩放，跟卡片一样大小，
    不管游戏分辨率、Windows 缩放是多少。"""
    return min(3.0, max(0.6, height / 1080))


def _rect_text(r) -> str:
    return f"{r.left},{r.top} {r.width}x{r.height}" if r else "无"


# 标签放在卡片下方时，标签底边在标题中心往下多少（1080P 像素）：卡片内框底边约在 248
BOTTOM_EDGE = 236
BOTTOM_GAP = 12    # 标签顶边和描述文字最低一行至少隔开这么多，不然这一组放回标题上方
BOTTOM_H = 30 + 18 * 2  # 放不放得下按两行标签的高度算：数据加载完多出一行时不会从下方跳回上方


class _Layer(QWidget):
    """盖在游戏或客户端上的透明层，对鼠标完全透明。

    按物理像素摆放（和截图同一套坐标），每次都读回窗口实际位置再画：以前按主显示器的缩放比例
    换算成 Qt 坐标再摆、从不核对，Windows 缩放不是 100%（例如 2K 屏 125%、游戏开 1080P 窗口）时
    有玩家看到标签放大、偏到卡片外面。位置或缩放有变化时记一行日志，方便查问题。"""

    name = "标注层"

    def __init__(self):
        super().__init__()
        _flags(self, click_through=True)
        self.origin = QPoint(0, 0)
        self.target_h = 1080
        self._diag = None

    def showEvent(self, e):
        win.exclude_from_capture(int(self.winId()))
        super().showEvent(e)

    def _scale(self) -> float:
        return self.devicePixelRatioF() if win.IS_WINDOWS else _dpr()

    def _place(self, rect, target_hwnd: int = 0) -> None:
        self.target_h = rect.height
        if not win.IS_WINDOWS:
            geo = QRect(_logical(rect.left), _logical(rect.top), _logical(rect.width), _logical(rect.height))
            if self.geometry() != geo:
                self.setGeometry(geo)
            self.origin = QPoint(rect.left, rect.top)
            if not self.isVisible():
                self.show()
            return
        if not self.isVisible():
            self.show()
        hwnd = int(self.winId())
        actual = win.window_rect(hwnd)
        if actual != rect:
            win.set_window_rect(hwnd, rect)
            actual = win.window_rect(hwnd) or rect
        self.origin = QPoint(actual.left, actual.top)
        key = (rect, actual, round(self._scale(), 3))
        if key != self._diag:
            self._diag = key
            screens = "；".join(
                f"{sc.name()} {sc.geometry().x()},{sc.geometry().y()} {sc.geometry().width()}x{sc.geometry().height()}"
                f" 缩放{sc.devicePixelRatio():g}" for sc in QGuiApplication.screens())
            (log.warning if actual != rect else log.info)(
                "%s：要盖住 %s，实际 %s，浮层缩放 %g；目标窗口 %s；本程序 %s；屏幕 %s", self.name,
                _rect_text(rect), _rect_text(actual), self._scale(), win.dpi_info(target_hwnd) or "?",
                win.dpi_info() or "?", screens)


class BadgeLayer(_Layer):
    """覆盖游戏客户区的透明层，只画三张卡的标签。"""

    name = "三选一标签"

    def __init__(self):
        super().__init__()
        self.snapshot: dict = {}
        # 放下方时：先看每张卡的描述写到哪（cardspace.desc_end），量出来之前不画
        self._room_key = None
        self._ends: list | None = None
        self._first_ends: list | None = None
        self._room_logged = None

    def update_from(self, snap: dict, rect, hwnd: int = 0) -> None:
        self.snapshot = snap
        ev, offer = snap.get("evaluation"), snap.get("offer")
        if not ev or not offer or rect is None:
            self._room_key = None
            self.hide()
            return
        self._place(rect, hwnd)
        if snap.get("settings", {}).get("badge_position") == "bottom":
            boxes = [tuple(b) if b else None for b in offer.get("boxes") or []]
            key = (tuple(offer.get("slots") or []), tuple(boxes), rect)
            if key != self._room_key:  # 新的一组卡（或刷新了一张、窗口变了）：重新量
                self._room_key, self._ends, self._first_ends = key, None, None
                self._measure(key, boxes, rect, first=True)
        self.update()

    def _measure(self, key, boxes, rect, first: bool) -> None:
        """截一张游戏画面看描述写到哪。打开了「录屏、截图时显示浮窗」时，自己画的标签也会被截到，
        先让标签层暂时不被截图（等 0.2 秒生效），量完再恢复。描述可能有淡入动画，0.6 秒后再量一次取最低，
        两次都量完才画（先画了再挪位置会跳一下）。"""
        hwnd = int(self.winId())
        capturable = win.capture_allowed()
        if capturable:
            win.hide_from_capture(hwnd)

        def grab():
            try:
                if key != self._room_key:
                    return
                from ..vision import capture, cardspace
                frame = capture.grab(rect)
                ends = [cardspace.desc_end(frame, b, (rect.left, rect.top)) for b in boxes]
            except Exception:
                log.exception("量卡片描述位置失败")
                ends = [None] * len(boxes)
            finally:
                if capturable:
                    win.restore_capture(hwnd)
            if key != self._room_key:
                return
            if first:
                self._first_ends = ends
                QTimer.singleShot(600, lambda: self._measure(key, boxes, rect, first=False))
                return
            prev = self._first_ends or [None] * len(ends)
            self._ends = [None if a is None or b is None else max(a, b) for a, b in zip(prev, ends)]
            self.update()

        QTimer.singleShot(200 if capturable else 0, grab)

    def paintEvent(self, _):
        snap = self.snapshot
        ev, offer = snap.get("evaluation"), snap.get("offer")
        if not ev or not offer:
            return
        show_win = snap.get("settings", {}).get("show_winrate", True)
        below = snap.get("settings", {}).get("badge_position") == "bottom"
        if below and self._ends is None:
            return  # 还没量完描述写到哪：先不画（约 0.6 秒）
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 下面都按「1080P 像素」算：标签大小跟着卡片走
        unit = _unit(self.target_h)
        p.scale(unit / self._scale(), unit / self._scale())
        ox, oy = self.origin.x(), self.origin.y()
        f_big = QFont("Microsoft YaHei UI", 12, QFont.Bold)
        f_small = QFont("Microsoft YaHei UI", 9)
        boxes = offer.get("boxes") or []
        known = [b for b in boxes if b]
        items = []
        for r in ev["slots"]:
            i = r["index"]
            box = boxes[i] if i < len(boxes) else None
            if box is None and len(known) >= 2:
                continue
            if box is None:
                continue
            x0 = (box[0] - ox) / unit
            x1 = (box[2] - ox) / unit
            y0 = (box[1] - oy) / unit
            w = max(170.0, x1 - x0)
            cx = (x0 + x1) / 2
            label, color = VERDICT.get(r["verdict"], VERDICT["unknown"])
            if r.get("basis") == "excluded":  # 尊我为王这类：写明原因，免得以为是助手没数据
                label = NODATA_NOTE
            if r.get("thin") == "too_few":
                label = "样本太少 · 建议刷新"
            lines = []
            if r.get("score") is not None:
                s = f"{r['score']:+.1f}"
                if show_win and r.get("win") is not None:
                    s += f" · 胜率{_pct(r['win'])}"
                lines.append(s)
            meta = []
            if r.get("rarity") and r.get("rank"):
                metric = "胜率" if r.get("rank_basis") == "hero_rarity_win_rate" else ""
                meta.append(f"{RARITY_CN.get(r['rarity'], '')}{metric}第{r['rank']}/{r.get('rank_total') or '?'}")
            if r.get("tier"):
                meta.append(f"{r['tier']}级")
            if r.get("basis") == "tier_only":
                meta.append("按官方层级")
            if r.get("basis") == "pbe" and r.get("pbe_games"):  # 新海克斯先用测试服数据：直接给局数和胜率，看得出高低
                meta.append(f"测试服 {r['pbe_games']} 局 胜率{_pct(r['pbe_win'])}")
            if r.get("thin") == "few":
                meta.insert(0, "样本偏少")
            if meta:
                lines.append(" · ".join(meta))
            h = 30 + 18 * len(lines)
            yc = ((box[1] + box[3]) / 2 - oy) / unit
            end = self._ends[i] if below and i < len(self._ends) else None
            items.append((r, label, color, lines, cx, w, h, y0, yc, end))
        # 设置里选了放下方：三张都放得下（描述最低一行和标签隔开 BOTTOM_GAP）才放下方，
        # 有一张描述太长就这一组都放标题上方，不挡海克斯的内容，三张也不会一上一下
        fits = below and bool(items) and all(end is not None and end + BOTTOM_GAP <= BOTTOM_EDGE - max(h, BOTTOM_H)
                                             for *_, h, _y0, _yc, end in items)
        if below and self._room_logged != (self._room_key, fits):
            self._room_logged = (self._room_key, fits)
            log.info("三选一标签放下方：描述最低到 %s（1080P 像素，标题中心往下），%s",
                     [None if e is None else round(e) for e in self._ends],
                     "放得下" if fits else "有一张放不下，这组放标题上方")
        for r, label, color, lines, cx, w, h, y0, yc, end in items:
            if fits:  # 描述文字下面、卡片底边上面那块空白
                rect = QRect(int(cx - w / 2), int(yc + BOTTOM_EDGE - h), int(w), int(h))
            else:  # 默认放在标题正上方（压在海克斯图标下半部分）：不挡名字、描述和下方的刷新按钮
                rect = QRect(int(cx - w / 2), int(y0 - 4 - h), int(w), int(h))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(10, 12, 16, 215))
            p.drawRoundedRect(rect, 6, 6)
            p.setPen(QPen(color, 2 if r["verdict"] == "best" else 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)
            p.setFont(f_big)
            p.setPen(color)
            p.drawText(QRect(rect.x(), rect.y() + 4, rect.width(), 24), Qt.AlignHCenter, label)
            p.setFont(f_small)
            p.setPen(QColor(225, 228, 235))
            for k, ln in enumerate(lines):
                p.drawText(QRect(rect.x() + 4, rect.y() + 28 + 18 * k, rect.width() - 8, 18), Qt.AlignHCenter, ln)
        p.end()


GRADE_COLOR = {"S": QColor(240, 200, 90), "A": QColor(95, 211, 141), "B": QColor(98, 168, 255),
               "C": QColor(200, 204, 214), "D": QColor(255, 122, 107)}


def win_color(win) -> QColor:
    if win is None:
        return QColor(150, 150, 150)
    if win >= 0.53:
        return QColor(95, 211, 141)
    if win >= 0.50:
        return QColor(98, 168, 255)
    if win >= 0.48:
        return QColor(225, 228, 235)
    return QColor(255, 122, 107)


def champ_select_summary(cs: dict, show_win: bool = True) -> str:
    """浮窗/主界面用的一行汇总：待选栏按胜率排序，和自己当前英雄比较。"""
    mine = next((t for t in cs.get("team") or [] if t["hero_id"] == cs.get("mine")), None)
    my_win = (mine or {}).get("win")
    bench = sorted(cs.get("bench") or [], key=lambda b: -(b.get("win") or 0))
    items = []
    for b in bench:
        txt = f"{b.get('name') or b['hero_id']} {b.get('grade') or ''}"
        if show_win and b.get("win") is not None:
            txt += f" {_pct(b['win'])}"
        if my_win is not None and b.get("win") is not None and b["win"] > my_win:
            txt = f"<b style='color:#5fd38d'>{txt} ↑{(b['win'] - my_win) * 100:.1f}</b>"
        items.append(txt)
    if cs.get("cards"):
        cards = sorted(cs["cards"], key=lambda c: -(c.get("win") or 0))
        return "开局选英雄：" + "、".join(
            f"{c.get('name') or c['hero_id']} {c.get('grade') or ''}{' ' + _pct(c.get('win')) if show_win else ''}"
            for c in cards)
    head = "待选栏"
    if mine:
        head += f"（你：{mine.get('name')} {mine.get('grade') or ''}{' ' + _pct(my_win) if show_win else ''}）"
    return f"{head}：" + "、".join(items)


class ChampSelectLayer(_Layer):
    """选英雄界面：在预选栏和我方每个英雄头像旁标评级（国服排名换算 S–D）与胜率；
    预选栏里胜率比自己当前英雄高的标绿，最高的那个加金框（值得换）。
    覆盖客户端窗口，对鼠标完全透明，不挡点击。"""

    name = "选英雄标注"

    def __init__(self):
        super().__init__()
        self.cs: dict | None = None
        self.show_win = True

    def update_from(self, snap: dict, rect, hwnd: int = 0) -> None:
        cs = snap.get("champ_select")
        if not cs or rect is None:
            self.cs = None
            self.hide()
            return
        self.show_win = snap.get("settings", {}).get("show_winrate", True)
        self._place(rect, hwnd)
        if cs != self.cs:
            self.cs = cs
            self.update()

    def paintEvent(self, _):
        cs = self.cs
        if not cs:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        dpr = self._scale()
        bench = cs.get("bench") or []
        mine = next((t for t in cs.get("team") or [] if t["hero_id"] == cs.get("mine")), None)
        my_win = (mine or {}).get("win")
        better = [b for b in bench if b.get("win") is not None and my_win is not None and b["win"] > my_win]
        best = max(better, key=lambda b: b["win"]) if better else None
        # 开局英雄卡：胜率最高的那张加金框
        cards = [c for c in cs.get("cards") or [] if c.get("win") is not None]
        best_card = max(cards, key=lambda c: c["win"]) if len(cards) >= 2 else None
        ox, oy = self.origin.x(), self.origin.y()
        for kind in ("cards", "bench", "team"):
            for s in cs.get(kind) or []:
                x0, y0, x1, y1 = ((s["box"][0] - ox) / dpr, (s["box"][1] - oy) / dpr,
                                  (s["box"][2] - ox) / dpr, (s["box"][3] - oy) / dpr)
                pw = x1 - x0
                # 字号跟头像大小走：不同分辨率、缩放比例、客户端尺寸下比例一致
                px = max(9, int(pw * {"bench": 0.24, "team": 0.21, "cards": 0.1}[kind]))
                big = QFont("Microsoft YaHei UI")
                big.setPixelSize(px)
                big.setBold(True)
                small = QFont("Microsoft YaHei UI")
                small.setPixelSize(max(9, int(px * 0.85)))
                win = s.get("win")
                win_txt = _pct(win) if self.show_win else ""
                grade = s.get("grade") or ""
                if kind == "bench":
                    color = QColor(95, 211, 141) if s in better else QColor(215, 218, 226)
                    r = QRect(int(x0 - 4), int(y1 + 2), int(pw + 8), int(px * 1.45))
                elif kind == "cards":  # 卡片正下方
                    color = win_color(win)
                    r = QRect(int(x0 + pw * 0.07), int(y1 + 6), int(pw * 0.86), int(px * 3.0))
                else:
                    color = win_color(win)
                    r = QRect(int(x1 + pw * 1.9), int((y0 + y1) / 2 - px * 1.5), int(pw * 1.2), int(px * 3.0))
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(10, 12, 16, 225))
                p.drawRoundedRect(r, 4, 4)
                if s is best or s is best_card:
                    gold = QPen(QColor(240, 200, 90), 2)
                    p.setPen(gold)
                    p.setBrush(Qt.NoBrush)
                    p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
                    p.drawRect(QRect(int(x0) - 2, int(y0) - 2, int(pw) + 4, int(y1 - y0) + 4))
                line = QRect(r.x(), r.y() + (int(px * 0.15) if kind != "bench" else 0), r.width(), int(px * 1.45))
                # 第一行：评级字母（按评级着色）+ 胜率
                fm_w = QFontMetrics(big)
                gap = fm_w.horizontalAdvance(" ")
                tw = fm_w.horizontalAdvance(grade) + (gap if grade and win_txt else 0) + fm_w.horizontalAdvance(win_txt)
                x = line.x() + (line.width() - tw) / 2
                p.setFont(big)
                if grade:
                    p.setPen(GRADE_COLOR.get(grade, QColor(200, 200, 200)))
                    p.drawText(QRect(int(x), line.y(), fm_w.horizontalAdvance(grade) + 2, line.height()),
                               Qt.AlignVCenter | Qt.AlignLeft, grade)
                    x += fm_w.horizontalAdvance(grade) + gap
                if win_txt:
                    p.setPen(color)
                    p.drawText(QRect(int(x), line.y(), fm_w.horizontalAdvance(win_txt) + 2, line.height()),
                               Qt.AlignVCenter | Qt.AlignLeft, win_txt)
                if kind == "team":
                    is_me = s["hero_id"] == cs.get("mine")
                    sub = f"第{s['rank']}/{s.get('total') or '?'}" if s.get("rank") else ""
                    p.setFont(small)
                    p.setPen(QColor(240, 200, 90) if is_me else QColor(170, 176, 190))
                    p.drawText(QRect(r.x(), line.bottom(), r.width(), r.bottom() - line.bottom()),
                               Qt.AlignHCenter | Qt.AlignTop, ("我 · " if is_me else "") + sub)
                elif kind == "cards":
                    sub = f"胜率第{s['rank']}/{s.get('total') or '?'}" if s.get("rank") else ""
                    p.setFont(small)
                    p.setPen(QColor(240, 200, 90) if s is best_card else QColor(170, 176, 190))
                    p.drawText(QRect(r.x(), line.bottom(), r.width(), r.bottom() - line.bottom()),
                               Qt.AlignHCenter | Qt.AlignTop, "★ 推荐" if s is best_card else sub)
        p.end()


class ControlPanel(QWidget):
    """右上角的小按钮 + 可展开详情。选英雄阶段跟随客户端窗口，进游戏后跟随游戏窗口。"""

    STYLE_BODY = ("QWidget#body{background:#101318ee;border:1px solid #2c3240;border-radius:8px}"
                  "QLabel{color:#e6e8ee;font:12px 'Microsoft YaHei UI'}"
                  "QLineEdit{background:#0c0e12;color:#e6e8ee;border:1px solid #2c3240;border-radius:4px;padding:3px 6px}"
                  "QPushButton{background:#1b2130;color:#e6e8ee;border:1px solid #2c3240;border-radius:4px;padding:2px 8px}"
                  "QPushButton:hover{border-color:#62a8ff}")

    def __init__(self, engine, corner: str = "top-right"):
        super().__init__()
        # 面板需要能输入（搜英雄），所以不设 WindowDoesNotAcceptFocus
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.engine = engine
        self.corner = corner
        self.expanded = False
        self.target_hwnd = 0      # 游戏窗口（进游戏后）或客户端窗口，点完浮窗把焦点还给它
        self._away_since = None   # 展开后鼠标离开浮窗的时刻
        self._hovered = False     # 这次展开后鼠标是否进过浮窗
        self._auto_opened = False
        self._auto_opened_for = None
        self._names = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        from .mainwindow import app_icon
        self.btn = QPushButton()
        self.btn.setIcon(app_icon())
        self.btn.setIconSize(QSize(30, 30))
        self.btn.setToolTip("海克斯大乱斗助手：点击展开/收起")
        self.btn.setFixedSize(40, 40)
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setStyleSheet("QPushButton{background:#171a21ee;color:#e7c35a;border:1px solid #3a4150;"
                               "border-radius:20px}"
                               "QPushButton:hover{border-color:#62a8ff}")
        self.btn.clicked.connect(self._click_toggle)

        self.body = QWidget()
        self.body.setObjectName("body")
        self.body.setAttribute(Qt.WA_StyledBackground)
        self.body.setStyleSheet(self.STYLE_BODY)
        self.body.setFixedWidth(360)
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(10, 10, 10, 10)
        bl.setSpacing(6)
        # 英雄：识别结果 / 候选 / 搜索手动选
        self.hero_label = QLabel()
        self.hero_label.setTextFormat(Qt.RichText)
        self.hero_label.setWordWrap(True)
        self.cands = QHBoxLayout()
        self.cands.setSpacing(4)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜英雄手动选择：提莫 / 提百万 / tm")
        self._completer_ready = False
        self.search.returnPressed.connect(self._search_pick)
        self.search.installEventFilter(self)
        self.info = QLabel()
        self.info.setTextFormat(Qt.RichText)
        self.info.setWordWrap(True)
        self.info.setOpenExternalLinks(False)
        self.info.linkActivated.connect(self._link)
        bl.addWidget(self.hero_label)
        bl.addLayout(self.cands)
        bl.addWidget(self.search)
        bl.addWidget(self.info)
        self.body.hide()
        lay.addWidget(self.btn, 0, Qt.AlignRight)
        lay.addWidget(self.body)
        self._cand_ids = []

    def showEvent(self, e):
        win.exclude_from_capture(int(self.winId()))
        win.add_companion(int(self.winId()))  # 点浮窗不算离开客户端，标注照常显示
        super().showEvent(e)

    def toggle(self):
        self.expanded = not self.expanded
        self.body.setVisible(self.expanded)
        self._away_since, self._hovered, self._auto_opened = None, False, False
        self.adjustSize()

    def _click_toggle(self):
        self.toggle()
        self.give_back_focus()

    def give_back_focus(self):
        # 等这次点击处理完再还焦点，否则 Windows 会在点击结束时又把浮窗激活
        QTimer.singleShot(0, lambda: win.give_focus(self.target_hwnd))

    def eventFilter(self, obj, ev):
        if obj is self.search and ev.type() == QEvent.KeyPress and ev.key() == Qt.Key_Escape:
            self.search.clear()
            self.give_back_focus()
            return True
        return super().eventFilter(obj, ev)

    def _typing(self) -> bool:
        return self.search.hasFocus() and self.isActiveWindow()

    def auto_hide(self, phase) -> None:
        """展开后鼠标离开一段时间就收起。选英雄阶段自动展开的，鼠标进来过或进了游戏才开始计时。"""
        if not self.expanded or not self.isVisible():
            return
        if self.frameGeometry().contains(QCursor.pos()):
            self._hovered, self._away_since = True, None
            return
        if self._typing() or (self._auto_opened and not self._hovered and phase == "client"):
            self._away_since = None
            return
        now = time.monotonic()
        if self._away_since is None:
            self._away_since = now
        elif now - self._away_since >= AUTO_HIDE_SECONDS:
            self.toggle()

    def _ensure_completer(self):
        heroes = self.engine.store.heroes
        if self._completer_ready or not heroes:
            return
        comp, self._names = hero_completer(self.engine.store, self)
        comp.activated.connect(self._completer_pick)
        self.search.setCompleter(comp)
        self._completer_ready = True

    def _completer_pick(self, text):
        hid = self._names.get(text)
        if hid:
            self.engine.set_hero(hid)
            self.search.clear()
            self.give_back_focus()

    def _search_pick(self):
        t = self.search.text().strip()
        if t:
            self.engine.set_hero(self._names.get(t, t))
            self.search.clear()
        self.give_back_focus()

    def _link(self, href):
        if href == "rescan":
            self.engine.rescan()
            self.give_back_focus()
        elif href == "quit":
            from .mainwindow import quit_app
            quit_app(self.engine)

    def place(self, rect) -> None:
        self.adjustSize()
        if rect is not None and win.IS_WINDOWS and self.isVisible():
            # 和标注层一样按物理像素摆：贴在游戏/客户端窗口的角上
            hwnd = int(self.winId())
            cur = win.window_rect(hwnd)
            if cur:
                k = self.devicePixelRatioF()
                x = rect.right - cur.width - round(12 * k) if self.corner == "top-right" else rect.left + round(12 * k)
                y = rect.top + round(90 * k)
                if (cur.left, cur.top) != (x, y):
                    win.set_window_rect(hwnd, win.Rect(x, y, cur.width, cur.height))
                return
        if rect is not None:
            left, top, right = _logical(rect.left), _logical(rect.top), _logical(rect.right)
        else:
            g = QGuiApplication.primaryScreen().availableGeometry()
            left, top, right = g.left(), g.top(), g.right()
        x = right - self.width() - 12 if self.corner == "top-right" else left + 12
        self.move(x, top + 90)

    def _set_candidates(self, cands):
        ids = [c.get("id") for c in cands]
        if ids == self._cand_ids:
            return
        self._cand_ids = ids
        while self.cands.count():
            w = self.cands.takeAt(0).widget()
            if w:
                w.deleteLater()
        for c in cands[:5]:
            b = QPushButton(c.get("name") or c.get("id"))
            b.clicked.connect(lambda _=False, hid=c.get("id"): (self.engine.set_hero(hid), self.give_back_focus()))
            self.cands.addWidget(b)
        self.cands.addStretch(1)

    def update_from(self, snap: dict) -> None:
        # 选英雄阶段识别到新英雄时自动展开一次，方便看胜率、出装、加点
        hid = snap.get("hero_id")
        if hid and snap.get("phase") == "client" and self._auto_opened_for != hid:
            self._auto_opened_for = hid
            if not self.expanded:
                self.toggle()
                self._auto_opened = True
        if not self.expanded:
            return
        self._ensure_completer()
        show_win = snap.get("settings", {}).get("show_winrate", True)
        h = snap.get("hero")
        d = self.engine.hero_panel(hid) if hid else None
        if h:
            r = (d or {}).get("rank") or {}
            b = self.engine.hero_brief(hid)
            rating = []
            if b.get("grade"):
                rating.append(f"评级 {b['grade']}")
            if b.get("rank"):
                rating.append(f"胜率第{b['rank']}/{b.get('total') or '?'}")
            if show_win and r.get("win") is not None:
                rating.append(f"胜率 {_pct(r['win'])}")
            if r.get("pick") is not None:
                rating.append(f"出场 {_pct(r['pick'])}")
            hero_html = f"<b style='color:#e7c35a;font-size:14px'>{h['name']}</b> {h['nick']}<br>{' · '.join(rating)}"
        else:
            hero_html = "<span style='color:#8b93a7'>未识别英雄</span>"
        if self.hero_label.text() != hero_html:
            self.hero_label.setText(hero_html)
        self._set_candidates(snap.get("candidates") or [])

        parts = []
        cs = snap.get("champ_select")
        if cs and (cs.get("bench") or cs.get("cards")):
            parts.append(champ_select_summary(cs, show_win))
        ev = snap.get("evaluation")
        if ev:
            parts.append(f"<b>{ev['advice']}</b>")
        if d:
            parts.extend(buildview.lines(d, self.engine.icons, show_win, size=22, compact=True))
            sk = buildview.skills_line(d, show_win)
            if sk:
                parts.append(sk)
            top = [a for a in d.get("augments", []) if a.get("delta") is not None and a.get("sample") != "low"]
            top.sort(key=lambda a: -a["delta"])
            if top:
                parts.append("推荐海克斯：" + "、".join(f"{a['name']}({a['delta']:+.1f})" for a in top[:5]))
        if snap.get("vision", {}).get("blank"):
            parts.append("<span style='color:#ff7a6b'>截图全黑，请把游戏改成无边框</span>")
        parts.append(f"<span style='color:#8b93a7'>{data_status_html(snap.get('data', {}))}</span>")
        parts.append("<a href='rescan' style='color:#62a8ff'>重新识别</a>　"
                     "<a href='quit' style='color:#ff7a6b'>退出助手</a>")
        html = "<br>".join(parts)
        if self.info.text() != html:
            self.info.setText(html)
            self.adjustSize()


class Overlay:
    def __init__(self, engine):
        self.engine = engine
        self.badges = BadgeLayer()
        self.champ = ChampSelectLayer()
        self.panel = ControlPanel(engine, engine.settings.overlay_corner)
        self.panel.show()
        self.autoshot = AutoShot(engine)  # 设置里开了自动截图时，三选一和选英雄时自动截整屏
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(250)

    def set_visible(self, on: bool):
        self.visible = on
        if on:
            self.panel.show()
        else:
            self.panel.hide()
            self.badges.hide()
            self.champ.hide()

    def _tick(self):
        if not getattr(self, "visible", True):
            return
        snap = self.engine.snapshot()
        if snap.get("vision", {}).get("blank") and not getattr(self, "_blank_warned", False):
            self._blank_warned = True  # 每次打开软件最多提醒一次
            notify = getattr(self, "notify", None)
            if notify:
                notify("截图是黑的：游戏可能是全屏模式。请在游戏设置 → 视频里，把窗口模式改成「无边框」。")
        game = win.find_game_window()
        grect = win.client_rect(game) if game else None
        self.badges.update_from(snap, grect, game)
        client = None if grect else win.find_client_window()
        crect = win.client_rect(client) if client else None
        # 标注层置顶：客户端不在前台（切到别的窗口）时隐藏，免得盖在别的程序上
        self.champ.update_from(snap, crect if crect and win.is_foreground(client) else None, client or 0)
        self.autoshot.tick(snap, grect, crect if crect and win.is_foreground(client) else None)
        self.panel.target_hwnd = game or client or 0
        self.panel.update_from(snap)
        self.panel.auto_hide(snap.get("phase"))
        # 选英雄阶段跟随客户端窗口，进游戏后跟随游戏窗口；两者都不在时不显示浮窗（用托盘和主界面）
        rect = grect or crect
        if rect is None:
            self.panel.hide()
            return
        if not self.panel.isVisible():
            self.panel.show()
        self.panel.place(rect)


SINGLE_INSTANCE = "aramhex-single-instance"


def version_notice(settings) -> str | None:
    """刚更新过（这次的版本和上次运行的不一样）就返回一句提示；第一次安装不提示。记下这次的版本。
    老版本没记过 last_version：有设置文件就算更新过来的。"""
    from .. import __version__
    last = settings.last_version
    if last == __version__:
        return None
    settings.last_version = __version__
    settings.save()
    return f"已更新到 {__version__}" if last or not getattr(settings, "fresh_install", True) else None


def run(make_engine, minimized: bool = False) -> int:
    """启动图形界面。已有实例在运行时，只通知它显示主窗口然后退出。"""
    import getpass
    import sys

    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    from .mainwindow import MainWindow, Tray, app_icon

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("海克斯大乱斗助手")
    app.setWindowIcon(app_icon())
    # 界面颜色是按浅色配的（卡片底色、灰字写死）：不跟随 Windows 深色模式，否则白字压在浅色卡片上看不见
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)

    name = f"{SINGLE_INSTANCE}-{getpass.getuser()}"

    def notify_running() -> bool:
        for _ in range(3):  # 已有的助手可能正忙，多试几次
            probe = QLocalSocket()
            probe.connectToServer(name)
            if probe.waitForConnected(1000):
                probe.write(b"show")
                probe.flush()
                probe.waitForBytesWritten(1000)
                return True
        return False

    if not single_instance.acquire(name):
        # 已经有助手在跑：让它显示主窗口。通知不到时多半是旧进程正在退出（例如更新后自动重启），
        # 等它退出后接班；等不到就不再起第二个
        if notify_running():
            return 0
        if single_instance.denied:  # 通知不到以管理员身份运行的那个：告诉玩家它在哪，不再起第二个
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(None, "海克斯大乱斗助手",
                                    "助手已经在运行（是以管理员身份打开的），请在屏幕右下角托盘里找它。\n"
                                    "想换成普通方式打开，先在托盘图标上右键退出它。")
            return 0
        if not single_instance.acquire(name, 10_000):
            return 0
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.listen(name)

    engine = make_engine()
    win.set_capture_allowed(engine.settings.allow_capture)  # 录屏、截图时是否显示浮窗
    engine.start()
    ov = Overlay(engine)
    ov.set_visible(engine.settings.overlay)
    main_win = MainWindow(engine, ov)
    tray = Tray(engine, main_win, ov)
    main_win.tray = tray
    tray.show()

    def on_second_instance():
        conn = server.nextPendingConnection()
        if conn:
            conn.readAll()
        main_win.show_and_raise()
        main_win.check_update()  # 用户再点一次图标：顺便查更新，不用退出重开

    server.newConnection.connect(on_second_instance)
    note = version_notice(engine.settings)
    if note:
        tray.showMessage("海克斯大乱斗助手", note, app_icon(), 8000)
    ov.notify = lambda text: tray.showMessage("海克斯大乱斗助手", text, app_icon(), 12000)
    if not engine.settings.seen_welcome:
        def welcome():
            engine.settings.seen_welcome = True
            engine.settings.save()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(main_win, "使用前", "请在游戏设置 → 视频里，把窗口模式改成「无边框」（或「窗口化」）。\n\n"
                                    "全屏模式下截不到画面，识别不了。")
        QTimer.singleShot(1200, welcome)
    failed = patch.take_failure()
    if failed:
        log.warning("上次更新没装上：%s", failed)
        tray.showMessage("海克斯大乱斗助手", "上次更新没有装上，已恢复原版本。打开主界面点「立即更新」再试一次。",
                         app_icon(), 10000)
    main_win.check_update()
    main_win.refresh_rank_if_stale()
    if not minimized:
        main_win.show_and_raise()
    app._keep = (ov, main_win, tray, server)  # 保持引用
    return app.exec()
