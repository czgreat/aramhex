"""游戏内悬浮层（PySide6）。

- 小按钮：常驻游戏窗口右上角，点一下展开/收起详情（英雄、推荐出装、加点、识别状态）。
  点完按钮、候选或链接后把焦点还给游戏，免得空格等按键落在浮窗上；展开后鼠标离开
  AUTO_HIDE_SECONDS 秒自动收起（正在搜索框里打字时不收；进游戏没认出英雄自动展开的，选好英雄前不收）。
- 卡片标签：识别到三选一时，在每张卡的标题下方显示「首选 / 可选 / 可考虑刷新」与依据。
  标签层对鼠标完全透明，不挡游戏点击；浮窗可以正常截图和录屏。
  只在用于识别的截图里涂掉助手绘制的区域，避免识别到自己的文字。
只有无边框/窗口化模式下悬浮层才能盖在游戏上面。
"""

from __future__ import annotations

import copy
import logging
import time
from html import escape

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen, QPixmap, QRegion
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

from .. import cloud, crashlog, recommend
from ..config import OVERLAY_BGS
from ..recommend import NODATA_NOTE
from ..vision import window as win
from .. import patch
from . import buildview, single_instance
from .autoshot import AutoShot

from .data_status import data_status_html
from .herosearch import hero_completer
from .hotkey import GameHotkey

log = logging.getLogger(__name__)

RARITY_CN = {"silver": "白银", "gold": "黄金", "prismatic": "棱彩"}
VERDICT = {"best": ("首选", QColor(95, 211, 141)), "ok": ("可选", QColor(98, 168, 255)),
           "reroll": ("可考虑刷新", QColor(255, 122, 107)), "unknown": ("未知", QColor(150, 150, 150)),
           "nodata": ("无数据", QColor(150, 150, 150))}
# 某张卡没认出的前几秒写「识别中」：多是三选一刚弹出、卡还在翻，字没读全，1–2 秒内就能认出（1.1.4）。
# 过了这个时间还认不出（新卡、被挡住）才写「未知」
READING_S = 2.0


AUTO_HIDE_SECONDS = 5
BUTTON_REVEAL_SECONDS = 1.5
# 1.1.7：游戏内按钮由 40 放大到 48（逻辑像素）；按需模式的感应范围在按钮外再留 24（原 8），更容易找到
GAME_BUTTON = 48
REVEAL_PAD = 24
# 1.1.7：对局里第一次海克斯选择结束之前，按钮一直显示，让玩家知道它在哪；选完 FIRST_OFFER_HIDE_SECONDS 秒后
# 按钮连同详情直接隐藏，之后鼠标移到原位置附近再出现。没认出英雄时详情一直开着提示手动选，不受这条影响。
# 中途才打开助手、等不到第一次选择时，进对局 PIN_MAX_SECONDS 秒后也不再固定显示（读条也算在对局里）。
FIRST_OFFER_HIDE_SECONDS = 0.5
PIN_MAX_SECONDS = 300


def _dpr() -> float:
    s = QGuiApplication.primaryScreen()
    return s.devicePixelRatio() if s else 1.0


def _logical(x: float) -> int:
    return int(round(x / _dpr()))


def _overflow(actual, rect) -> bool:
    """实际窗口超出要盖住的范围（哪怕 1 像素，可能已经伸到相邻显示器上）。"""
    return (actual.left < rect.left or actual.top < rect.top
            or actual.right > rect.right or actual.bottom > rect.bottom)


def _gap(actual, rect) -> int:
    """实际窗口比要盖住的范围小了多少像素（取各边最大）。"""
    return max(actual.left - rect.left, actual.top - rect.top,
               rect.right - actual.right, rect.bottom - actual.bottom, 0)


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
HINT_HEAD_H, HINT_ROW_H, HINT_NOTE_H = 24, 30, 28  # 命运之武器装备提示：标题行、每件装备、没有够格装备时那一行
HINT_ICON, HINT_PAD = 22, 8
HINT_TITLE = "首件传说装备"


def hint_title(hint) -> str:
    """命运之武器提示的标题；选了分段时带上用的是哪档（「前 5%」或「全体」）。"""
    band = hint.get("band_label")
    return f"{HINT_TITLE} · {band}" if band else HINT_TITLE


HINT_BG = QColor(16, 20, 28, 170)  # 半透明：只是浮在卡片空白处，不当遮罩用


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
        self._fit: tuple | None = None  # (要盖住的, 摆完的实际位置)：分数缩放下摆不准时记住，不再反复重摆

    def hideEvent(self, e):
        win.set_drawn(type(self).__name__, [])
        super().hideEvent(e)

    def _publish(self, rects, k: float) -> None:
        """登记这次画在哪：rects 是画布坐标（QRect），乘 k 再加窗口左上角就是屏幕物理像素。
        识别截图会把这些地方涂掉（浮窗不再对截图隐身）。"""
        ox, oy, pad = self.origin.x(), self.origin.y(), 2
        win.set_drawn(type(self).__name__, [
            win.Rect(int(ox + r.x() * k) - pad, int(oy + r.y() * k) - pad,
                     int(r.width() * k) + 2 * pad, int(r.height() * k) + 2 * pad) for r in rects])

    def _scale(self) -> float:
        return self.devicePixelRatioF() if win.IS_WINDOWS else _dpr()

    def _place_qt(self, rect) -> None:
        geo = QRect(_logical(rect.left), _logical(rect.top), _logical(rect.width), _logical(rect.height))
        if self.geometry() != geo:
            self.setGeometry(geo)
        self.origin = QPoint(rect.left, rect.top)
        if not self.isVisible():
            self.show()

    def _place(self, rect, target_hwnd: int = 0, target_h: int | None = None) -> None:
        self.target_h = target_h or rect.height
        if not win.IS_WINDOWS:
            return self._place_qt(rect)
        if not self.isVisible():
            self.show()
        hwnd = int(self.winId())
        actual = win.window_rect(hwnd)
        if actual is None:  # 读不到窗口位置（没有真窗口的无界面测试等）：按 Qt 坐标摆
            return self._place_qt(rect)
        fit = self._fit if self._fit and self._fit[0] == rect else None
        if not cloud.feature("overlay_fit"):  # 云控关掉：按 1.1.1 每次对不上就重摆
            if actual != rect:
                win.set_window_rect(hwnd, rect)
                actual = win.window_rect(hwnd) or rect
        elif actual != rect and (fit is None or actual != fit[1]):
            # 缩放 1.25/1.5 时 Qt 按逻辑像素取整，窗口会比要求多出 1 像素（双屏时伸到另一块屏上），
            # 原来每次刷新都再 SetWindowPos 一次。往里收掉多出的部分再摆一次；同一个目标只调一轮，
            # 之后窗口还在这一轮摆出的位置就不再动
            win.set_window_rect(hwnd, rect)
            actual = win.window_rect(hwnd) or rect
            if _overflow(actual, rect):
                inner = win.Rect(rect.left, rect.top, rect.width - max(0, actual.right - rect.right),
                                 rect.height - max(0, actual.bottom - rect.bottom))
                win.set_window_rect(hwnd, inner)
                actual = win.window_rect(hwnd) or inner
            self._fit = (rect, actual)
        self.origin = QPoint(actual.left, actual.top)
        key = (rect, actual, round(self._scale(), 3))
        if key != self._diag:
            self._diag = key
            screens = "；".join(
                f"{sc.name()} {sc.geometry().x()},{sc.geometry().y()} {sc.geometry().width()}x{sc.geometry().height()}"
                f" 缩放{sc.devicePixelRatio():g}" for sc in QGuiApplication.screens())
            (log.warning if _overflow(actual, rect) or _gap(actual, rect) > 2 else log.info)(
                "%s：要盖住 %s，实际 %s，浮层缩放 %g；目标窗口 %s；本程序 %s；屏幕 %s", self.name,
                _rect_text(rect), _rect_text(actual), self._scale(), win.dpi_info(target_hwnd) or "?",
                win.dpi_info() or "?", screens)


class BadgeLayer(_Layer):
    """盖在三张卡标签那一块的透明层（不铺满游戏画面），只画三张卡的标签。"""

    name = "三选一标签"
    # 标签和装备提示最远画到标题框外多少（1080P 像素）：左右按最宽的装备提示 340 的一半再留余量，
    # 上方留够标签（可能两行），下方到卡片底边 BOTTOM_EDGE；装备提示都在卡片说明那块
    BAND_SIDE, BAND_ABOVE, BAND_BELOW = 200, 300, BOTTOM_EDGE + 40
    EDGE = 4  # 离游戏画面边缘至少留几个物理像素

    def __init__(self):
        super().__init__()
        self.snapshot: dict = {}
        self.icons = None
        self._painted = None

    def hideEvent(self, e):
        self._painted = None
        super().hideEvent(e)

    def band(self, rect, boxes) -> "win.Rect":
        """标签层的位置：三张卡标题框往外扩一圈，限制在游戏画面里、离边缘 EDGE 像素。

        以前铺满整个游戏画面，Windows 缩放 150% 时 2560 宽的层被 Qt 取整成 2561，多出的 1 像素
        压到右边的副屏上；置顶的透明窗口横跨两块屏，游戏选海克斯时明显掉帧，有时切出去再切回来才恢复
        （10-24 多个双屏玩家反馈，日志里「要盖住 0,0 2560x1440，实际 0,0 2561x1440」）。
        只盖中间这一块，取整多一两个像素也碰不到屏幕边缘，盖住的面积也小得多。"""
        unit = _unit(rect.height)
        known = [b for b in boxes or [] if b]
        x0, y0 = rect.left + self.EDGE, rect.top + self.EDGE
        x1, y1 = rect.right - self.EDGE, rect.bottom - self.EDGE
        if known:
            x0 = max(x0, int(min(b[0] for b in known) - self.BAND_SIDE * unit))
            x1 = min(x1, int(max(b[2] for b in known) + self.BAND_SIDE * unit))
            y0 = max(y0, int(min(b[1] for b in known) - self.BAND_ABOVE * unit))
            y1 = min(y1, int(max(b[3] for b in known) + self.BAND_BELOW * unit))
        if x1 - x0 < 50 or y1 - y0 < 50:  # 框不在画面里（异常）：退回整个画面（仍离边缘 EDGE）
            x0, y0 = rect.left + self.EDGE, rect.top + self.EDGE
            x1, y1 = rect.right - self.EDGE, rect.bottom - self.EDGE
        return win.Rect(x0, y0, x1 - x0, y1 - y0)

    def update_from(self, snap: dict, rect, hwnd: int = 0) -> None:
        self.snapshot = snap
        ev, offer = snap.get("evaluation"), snap.get("offer")
        if not ev or not offer or rect is None:
            self.hide()
            return
        self._place(self.band(rect, offer.get("boxes")), hwnd, target_h=rect.height)
        # 内容不变时不重画；单列刷新只更新显示变化的标签，避免另外两张一起闪。
        geometry = (offer.get("boxes"), offer.get("padded"), self.origin.x(), self.origin.y(),
                    self.target_h, round(self._scale(), 3), self.size())
        cards = {}
        factor = _unit(self.target_h) / self._scale()
        for r, label, color, lines, badge, hint_rect, *_ in self._badge_layout(snap):
            region = QRegion()
            for bounds in (badge, hint_rect):
                if bounds is not None:
                    logical = QRectF(bounds.x()*factor, bounds.y()*factor,
                                     bounds.width()*factor, bounds.height()*factor)
                    region |= logical.toAlignedRect().adjusted(-2, -2, 2, 2)
            hint = r.get("item_hint")
            hint_win = snap.get("settings", {}).get("show_winrate", True) if hint else None
            visual = (label, color.rgba(), lines, hint, hint_win, badge, hint_rect)
            cards[r["index"]] = (copy.deepcopy(visual), region)
        previous = self._painted
        self._painted = (copy.deepcopy(geometry), cards)
        if previous is None or previous[0] != geometry:
            self.update()
        else:
            dirty = QRegion()
            for index in previous[1].keys() | cards.keys():
                before, after = previous[1].get(index), cards.get(index)
                if before is None or after is None or before[0] != after[0]:
                    if before is not None:
                        dirty |= before[1]
                    if after is not None:
                        dirty |= after[1]
            if not dirty.isEmpty():
                self.update(dirty)

    def _badge_layout(self, snap):
        ev, offer = snap["evaluation"], snap["offer"]
        show_win = snap.get("settings", {}).get("show_winrate", True)
        # 选了放下方就一直放下方：以前先截图量描述写到哪、放不下整组回标题上方，卡片淡入时半透明，
        # 后面的血条和名字透出来被当成描述，刷新后常误判跳回上方，玩家以为设置没生效
        below = snap.get("settings", {}).get("badge_position") == "bottom"
        # 下面都按「1080P 像素」算：标签大小跟着卡片走
        unit = _unit(self.target_h)
        ox, oy = self.origin.x(), self.origin.y()
        f_big = QFont("Microsoft YaHei UI", 12, QFont.Bold)
        f_small = QFont("Microsoft YaHei UI", 9)
        fm_big, fm_small = QFontMetrics(f_big, self), QFontMetrics(f_small, self)
        boxes = offer.get("boxes") or []
        known = [b for b in boxes if b]
        items = []
        for r in ev["slots"]:
            i = r["index"]
            pending = offer.get("pending_columns") or ()
            if i < len(pending) and pending[i]:
                continue  # 不画待确认列的旧分数、评级及装备提示；另外两列保持可见。
            box = boxes[i] if i < len(boxes) else None
            if box is None and len(known) >= 2:
                continue
            if box is None:
                continue
            x0 = (box[0] - ox) / unit
            x1 = (box[2] - ox) / unit
            y0 = (box[1] - oy) / unit
            cx = (x0 + x1) / 2
            label, color = VERDICT.get(r["verdict"], VERDICT["unknown"])
            unread = offer.get("unread_since") or ()
            since = unread[i] if i < len(unread) and not r.get("augment_id") else None
            if since is not None and time.monotonic() - since < READING_S:
                label = "识别中…"
            if r["verdict"] == "best" and r.get("sample_warning"):
                label = "相对较好 · 仅供参考"
            if r.get("basis") == "excluded":  # 尊我为王这类：写明原因，免得以为是助手没数据
                label = NODATA_NOTE
            if r.get("thin") == "too_few":
                label = "样本太少 · 建议刷新"
            lines = []
            if r.get("score") is not None:
                s = f"{r['score']:+.1f}"
                if show_win and recommend.win_text(r):
                    s += " · " + recommend.win_text(r)
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
            if r.get("thin") == "few" or r.get("sample_warning") and r.get("thin") != "too_few":
                meta.insert(0, "样本偏少")
            if not ev.get("hero_id"):  # 只在浮窗里写的话进游戏 5 秒就收起，玩家常常不知道是按全英雄算的
                meta.insert(0, "未识别英雄 · 按全英雄数据")
            if meta:
                lines.append(" · ".join(meta))
            # 标题框只是标题文字那么宽（还没校准时）：字长的（尊我为王那句）按字撑开，不让底框比字窄
            w = max(170.0, x1 - x0, fm_big.horizontalAdvance(label) + 20,
                    max((fm_small.horizontalAdvance(ln) for ln in lines), default=0) + 16)
            h = 30 + 18 * len(lines)
            yc = ((box[1] + box[3]) / 2 - oy) / unit
            items.append((r, label, color, lines, cx, w, h, y0, yc))
        result = []
        for r, label, color, lines, cx, w, h, y0, yc in items:
            if below:  # 描述文字下面、卡片底边上面那块空白
                rect = QRect(int(cx - w / 2), int(yc + BOTTOM_EDGE - h), int(w), int(h))
            else:  # 默认放在标题正上方（压在海克斯图标下半部分）：不挡名字、描述和下方的刷新按钮
                # 标题校准框上下各留了约 2/9 框高的空白；利用上侧空白下移，露出图标下方的技能键。
                # 保留 4 个基准像素的文字间距，小标题框不强行下移到文字里。
                title_pad = (boxes[r["index"]][3] - boxes[r["index"]][1]) / unit * 2 / 9
                shift = min(12.0, max(0.0, title_pad)) if offer.get("padded") else 0.0
                rect = QRect(int(cx - w / 2), int(y0 - 4 + shift - h), int(w), int(h))
            hint = r.get("item_hint")
            hr = self._item_hint_rect(hint, cx, y0, yc, rect, below, snap.get("settings", {}).get("show_winrate", True)) if hint else None
            result.append((r, label, color, lines, rect, hr, cx, y0, yc))
        return result

    def paintEvent(self, _):
        snap = self.snapshot
        if not snap.get("evaluation") or not snap.get("offer"):
            win.set_drawn(type(self).__name__, [])
            return
        show_win = snap.get("settings", {}).get("show_winrate", True)
        below = snap.get("settings", {}).get("badge_position") == "bottom"
        unit = _unit(self.target_h)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(unit / self._scale(), unit / self._scale())
        f_big = QFont("Microsoft YaHei UI", 12, QFont.Bold)
        f_small = QFont("Microsoft YaHei UI", 9)
        fm_small = QFontMetrics(f_small, self)
        drawn = []
        for r, label, color, lines, rect, hr, cx, y0, yc in self._badge_layout(snap):
            drawn.append(rect)
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
            hint = r.get("item_hint")
            if hint:
                self._paint_item_hint(p, hint, hr, fm_small, show_win)
                drawn.append(hr)
        p.end()
        self._publish(drawn, unit)

    @staticmethod
    def _item_hint_label(row):
        style = row.get("style")
        return f"{style} · {row['name']}" if style and style != "不拆" else row["name"]

    @staticmethod
    def _item_hint_stat(row, hint, show_win):
        """装备右侧那段数字：默认只有相对英雄基准的加减，其余字段由数据决定。"""
        parts = ["样本偏少"] if row.get("low_sample") else []
        if show_win and row.get("win") is not None:
            if hint.get("show_rate", True) is True:
                parts.append(f"胜率 {_pct(row['win'])}")
            if row.get("delta") is not None:
                parts.append(f"{round(row['delta'], 1) or 0.0:+.1f}")  # or 0.0：避免显示 -0.0
        if hint.get("show_pick_rate") is True and row.get("pick") is not None:
            parts.append(f"选取 {'<1%' if row['pick'] < 0.01 else f'{row['pick'] * 100:.0f}%'}")
        if hint.get("show_sample_size") is True and row.get("games"):
            parts.append(f"{row['games']:,}局")
        return " · ".join(parts)

    def _item_hint_rect(self, hint, cx, y0, yc, badge, badge_below, show_win=True):
        """命运之武器的装备提示：大小只包住内容，不盖说明文字。
        标签在标题上方时，提示放卡片底部空白处；标签在底部时，那块空白放不下，提示改放标题上方的图标区域。"""
        title_bottom = int(2 * yc - y0 + 4)  # 标题框下沿
        bottom = badge.y() - 6 if badge_below else int(yc + BOTTOM_EDGE - 8)
        fm = QFontMetrics(QFont("Microsoft YaHei UI", 9), self)
        rows = hint["items"][:3]
        if not rows:  # 没有够格的装备：说明照常显示，只在底部写一行
            width = min(badge.width(), fm.horizontalAdvance(hint.get("note") or "") + 2 * HINT_PAD)
            return QRect(int(cx - width / 2), bottom - HINT_NOTE_H, width, HINT_NOTE_H)
        widest = max(fm.horizontalAdvance(hint_title(hint)), *(
            HINT_ICON + 6 + fm.horizontalAdvance(self._item_hint_label(row)) + 12 + fm.horizontalAdvance(self._item_hint_stat(row, hint, show_win))
            for row in rows))
        width = min(badge.width(), widest + 2 * HINT_PAD)
        height = HINT_HEAD_H + HINT_ROW_H * len(rows) + 4
        top = int(y0 - 4 - height) if badge_below else max(title_bottom, bottom - height)
        return QRect(int(cx - width / 2), top, width, height)

    def _paint_item_hint(self, p, hint, rect, fm, show_win):
        p.setPen(Qt.NoPen)
        p.setBrush(HINT_BG)
        p.drawRoundedRect(rect, 6, 6)
        rows = hint["items"][:3]
        if not rows:
            p.setPen(QColor(170, 176, 190))
            p.drawText(rect, Qt.AlignCenter, hint.get("note") or "")
            return
        x, width = rect.x() + HINT_PAD, rect.width() - 2 * HINT_PAD
        p.setPen(QColor(231, 195, 90))
        p.drawText(QRect(x, rect.y() + 4, width, 18), Qt.AlignLeft, hint_title(hint))
        row_h = min(HINT_ROW_H, (rect.height() - HINT_HEAD_H - 4) // len(rows))
        for i, row in enumerate(rows):
            y = rect.y() + HINT_HEAD_H + i * row_h
            size = min(HINT_ICON, row_h - 4)
            path = self.icons.local("item", row["item"], row.get("icon")) if self.icons else None
            image = QPixmap(path) if path else QPixmap()
            if not image.isNull():
                p.drawPixmap(QRect(x, y, size, size), image)
            text_x = x + size + 6
            text_w = x + width - text_x
            stat = self._item_hint_stat(row, hint, show_win)
            stat_w = min(fm.horizontalAdvance(stat), text_w)
            p.setPen(QColor(240, 240, 245))
            name_w = max(0, text_w - stat_w - 12)
            p.drawText(QRect(text_x, y + 2, name_w, 18), Qt.AlignLeft,
                       fm.elidedText(self._item_hint_label(row), Qt.ElideRight, name_w))
            delta = row.get("delta") if show_win else None
            p.setPen(QColor(190, 196, 208) if delta is None or round(delta, 1) == 0
                     else QColor(95, 211, 141) if delta > 0 else QColor(255, 122, 107))
            p.drawText(QRect(x + width - stat_w, y + 2, stat_w, 18), Qt.AlignRight,
                       fm.elidedText(stat, Qt.ElideRight, stat_w))


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
            win.set_drawn(type(self).__name__, [])
            return
        drawn = []
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
                drawn.append(r)
                if s is best or s is best_card:
                    gold = QPen(QColor(240, 200, 90), 2)
                    p.setPen(gold)
                    p.setBrush(Qt.NoBrush)
                    p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
                    frame = QRect(int(x0) - 2, int(y0) - 2, int(pw) + 4, int(y1 - y0) + 4)
                    p.drawRect(frame)
                    # 金框只登记四条边（中间是头像，识别要看）
                    drawn += [QRect(frame.x(), frame.y(), frame.width(), 2), QRect(frame.x(), frame.bottom() - 1, frame.width(), 2),
                              QRect(frame.x(), frame.y(), 2, frame.height()), QRect(frame.right() - 1, frame.y(), 2, frame.height())]
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
        self._publish(drawn, dpr)


def client_badge(rect) -> tuple[int, int, int]:
    """客户端窗口（物理像素）→ 大厅图标的中心 x、y 和直径（物理像素）。
    客户端界面按窗口大小整体缩放，所以用 1280×720 下量的位置乘以「窗口高度 / 720」。"""
    ui = cloud.get().get("ui") or {}

    def num(key, default, lo, hi):
        v = ui.get(key, default)
        return v if isinstance(v, (int, float)) and lo <= v <= hi else default
    s = rect.height / 720
    right = num("client_badge_right", 60, 10, 400)
    top = num("client_badge_top", 53, 10, 300)
    size = num("client_badge_size", 26, 16, 60)
    return round(rect.right - right * s), round(rect.top + top * s), round(size * s)


class ControlPanel(QWidget):
    """右上角的小按钮 + 可展开详情。选英雄阶段跟随客户端窗口，进游戏后跟随游戏窗口。

    图标和详情是两个窗口（1.1.1）：以前放在一个窗口里，展开/收起时窗口在图标大小和 360 宽之间变，
    Qt 先按左边不动撑大、再摆回右对齐，中间约 30 毫秒图标跳到别处（231 逐帧采样实测），玩家看到图标闪一下。
    现在图标窗口大小、位置都不因展开收起而变，详情窗口贴在图标下面单独显示、隐藏。"""

    # 详情的背景（详情里的「背景」按钮，1.2.0）。Qt 把 8 位颜色读成 #AARRGGBB：1.1.9 起写的
    # #101318ee 实际是透明度 0x10（约 6%）的蓝色，详情几乎是透明的，玩家说看不清字。「透明」保持这个样子（1.1.9），
    # 半透明约 70%，不透明 100%，颜色都是本来想要的 #101318
    PANEL_BG = {"standard": "#101318ee", "dark": "#b3101318", "solid": "#ff101318"}
    bg_picked = Signal(str)  # 详情里点了「背景」按钮：要换成的那档，主界面存设置、生效
    STYLE_BODY = ("QWidget#body{background:%s;border:1px solid #2c3240;border-radius:8px}"
                  "QLabel{color:#e6e8ee;font:12px 'Microsoft YaHei UI'}"
                  "QLineEdit{background:#0c0e12;color:#e6e8ee;border:1px solid #2c3240;border-radius:4px;padding:3px 6px}"
                  "QPushButton{background:#1b2130;color:#e6e8ee;border:1px solid #2c3240;border-radius:4px;padding:2px 8px}"
                  "QPushButton:hover{border-color:#62a8ff}")

    BTN_STYLE = ("QPushButton{background:#171a21ee;color:#e7c35a;border:1px solid #3a4150;border-radius:%dpx}"
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
        self._typing_on = False   # 1.1.9：点进了搜索框、详情窗口真拿到了前台；选好、Esc、收起、玩家点回别处时结束
        self._typing_from = 0     # 1.2.0：点进搜索框之前在前台的窗口（从游戏点进来的才按游戏在前台画）
        self._away_since = None   # 展开后鼠标离开浮窗的时刻
        self._hovered = False     # 这次展开后鼠标是否进过浮窗
        self._auto_opened = False
        self._auto_opened_for = None
        self._missing_hero_notified = False
        self._hold_for_hero = False  # 进游戏没认出英雄自动展开的：选好英雄或玩家手动收起前不自动收
        self._offer_seen = False       # 这局出现过海克斯选择
        self._ingame_since = None      # 这局进对局的时刻：从这时起按钮固定显示
        self._unpin_at = None          # 第一次选择结束后，到这个时刻隐藏按钮
        self._pin_over = False         # 这局按钮固定显示已结束
        self._reveal_until = 0.0
        self._reveal_target = None
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
        self.btn.setStyleSheet(self.BTN_STYLE % 20)
        self.btn.clicked.connect(self._click_toggle)

        # 详情：单独的窗口（属于图标窗口，跟着它一起关），贴在图标下面
        self.body_win = QWidget(self, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.body_win.setAttribute(Qt.WA_TranslucentBackground)
        self.body_win.setAttribute(Qt.WA_ShowWithoutActivating)
        bw_lay = QVBoxLayout(self.body_win)
        bw_lay.setContentsMargins(0, 0, 0, 0)
        self.body = QWidget()
        self.body.setObjectName("body")
        self.body.setAttribute(Qt.WA_StyledBackground)
        self.bg_level = "standard"
        self.body.setStyleSheet(self.STYLE_BODY % self.PANEL_BG[self.bg_level])
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
        self.body_win.installEventFilter(self)  # 打字中途详情失去前台
        self.confirm_hero = QPushButton("确认")
        self.confirm_hero.clicked.connect(self._search_pick)
        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.confirm_hero)
        self.search_feedback = QLabel("未找到英雄，请输入英雄名或从候选项选择")
        self.search_feedback.setWordWrap(True)
        self.search_feedback.setStyleSheet("color:#e7c35a")
        self.search_feedback.hide()
        self.info = QLabel()
        self.info.setTextFormat(Qt.RichText)
        self.info.setWordWrap(True)
        self.info.setOpenExternalLinks(False)
        self.info.linkActivated.connect(self._link)
        bl.addWidget(self.hero_label)
        bl.addLayout(self.cands)
        bl.addLayout(search_row)
        bl.addWidget(self.search_feedback)
        bl.addWidget(self.info)
        # 详情的背景：点一下换下一档，只有这里能换（云控 features.overlay_bg 关了不显示）
        self.bg_btn = QPushButton()
        self.bg_btn.setToolTip("详情的背景，点一下换：透明 → 半透明 → 不透明。\n"
                               "看不清字就换成不透明，会挡住后面的游戏画面。")
        self.bg_btn.clicked.connect(self._next_bg)
        self._show_bg_btn()
        bg_row = QHBoxLayout()
        bg_row.addStretch(1)
        bg_row.addWidget(self.bg_btn)
        bl.addLayout(bg_row)
        bw_lay.addWidget(self.body)
        lay.addWidget(self.btn)
        self._cand_ids = []

    def showEvent(self, e):
        win.add_companion(int(self.winId()))  # 点浮窗不算离开客户端，标注照常显示
        self._no_activate()
        super().showEvent(e)
        self._publish()

    def _no_activate(self) -> None:
        """1.1.9：点图标、点详情都不抢前台，键盘一直归游戏（游戏丢一次前台再拿回来，可能被 Windows 改成合成显示、锁帧）。
        只有点进搜索框打字时详情窗口才临时能当前台（_start_typing），选好、Esc、收起、点回别处时改回。
        云控 features.panel_no_activate=false 退回 1.1.8：点了浮窗就变前台，点完再还给游戏。"""
        on = cloud.feature("panel_no_activate")
        win.set_no_activate(int(self.winId()), on)
        win.set_no_activate(int(self.body_win.winId()), on and not self._typing_on)

    def owns_foreground(self) -> bool:
        """前台是不是图标、详情或搜索框的补全列表（玩家点进搜索框在打字）。"""
        return self.isVisible() and win.foreground_window() in self._panel_hwnds()

    def typing_source(self) -> int | None:
        """玩家点进搜索框、现在还在详情或补全列表里打字时，点进来之前在前台的窗口；没在打字、不知道从哪来返回 None。"""
        return (self._typing_from or None) if self._typing_on and self.owns_foreground() else None

    def _panel_hwnds(self) -> set[int]:
        """图标、详情和搜索框的补全列表。"""
        out = {int(self.winId()), int(self.body_win.winId())}
        c = self.search.completer()
        if c is not None and c.popup().isVisible():
            out.add(int(c.popup().winId()))
        return out

    def _sync_no_activate(self) -> None:
        """每轮摆放、详情失去前台时：打字中途玩家点回游戏或别处，只把样式改回去，不抢前台；
        云控开关改了，已经显示的浮窗也在下一次点击前跟着改。"""
        if self._typing_on and win.foreground_window() not in self._panel_hwnds():
            self._typing_on = False
            log.debug("浮窗：打字中途前台换走了，详情改回不抢前台")
        self._no_activate()

    def _start_typing(self) -> None:
        hb = int(self.body_win.winId())
        prev = win.foreground_window()
        if prev not in self._panel_hwnds():
            self._typing_from = prev
        elif not self._typing_on:  # 点之前详情已在前台（云控 panel_no_activate=false 时点了就变前台）：不知道从哪来
            self._typing_from = 0
        # 打字中途又点了一下搜索框：留着最初的
        self._typing_on = True
        if win.set_no_activate(hb, False):
            # 刚点了本进程的窗口，Windows 允许换前台；不看 isActiveWindow：点过之后 Qt 就当它是活动窗口了，其实前台还是游戏
            win.give_focus(hb)
        if win.foreground_window() != hb:
            self._typing_on = False  # 没换成（Windows 拒绝）：改回去，免得之后点详情别处又抢前台
            self._no_activate()
        self.search.setFocus()

    def hideEvent(self, e):
        self._typing_on = False
        self.body_win.hide()
        win.set_drawn("ControlPanel", [])
        super().hideEvent(e)

    def moveEvent(self, e):
        super().moveEvent(e)
        self._publish()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._publish()

    def _publish(self) -> None:
        """图标和展开的详情登记成「自己画的」，识别截图时涂掉（详情里有英雄名、海克斯名）。"""
        if not self.isVisible():
            return
        k = self.devicePixelRatioF() or 1.0
        rects = []
        for w in [self] + ([self.body_win] if self.body_win.isVisible() else []):
            r = win.window_rect(int(w.winId())) if win.IS_WINDOWS else None
            if r is None:
                g = w.geometry()
                r = win.Rect(round(g.x() * k), round(g.y() * k), round(g.width() * k), round(g.height() * k))
            rects.append(win.Rect(r.left - 2, r.top - 2, r.width + 4, r.height + 4))
        win.set_drawn("ControlPanel", rects)

    def _place_body(self) -> None:
        """详情窗口贴在图标下面：浮窗在右上角时右边和图标对齐，在左上角时左边对齐。"""
        bw = self.body_win
        if not self.expanded or not self.isVisible():
            if bw.isVisible():
                bw.hide()
                self._typing_on = False
                self._no_activate()
                self._publish()
            return
        bw.adjustSize()
        k = self.devicePixelRatioF() or 1.0
        icon = win.window_rect(int(self.winId())) if win.IS_WINDOWS else None
        if icon:
            hb = int(bw.winId())
            w, h = round(bw.width() * k), round(bw.height() * k)
            x = icon.left + icon.width - w if self.corner == "top-right" else icon.left
            target = win.Rect(x, icon.top + icon.height + round(4 * k), w, h)
            if win.window_rect(hb) != target:
                win.set_window_rect(hb, target)  # 还没显示时先摆好再显示，不会在别处闪一下
            if not bw.isVisible():
                win.add_companion(hb)  # 点详情不算离开客户端
                self._no_activate()
                bw.show()
                if win.window_rect(hb) != target:  # Qt 显示时按它记的位置又挪了：摆回来
                    log.debug("浮窗详情：显示后位置 %s，应为 %s", _rect_text(win.window_rect(hb)), _rect_text(target))
                    win.set_window_rect(hb, target)
        else:
            x = self.x() + self.width() - bw.width() if self.corner == "top-right" else self.x()
            bw.move(x, self.y() + self.height() + 4)
            bw.show()
        self._publish()

    def toggle(self):
        self.expanded = not self.expanded
        self._away_since, self._hovered, self._auto_opened, self._hold_for_hero = None, False, False, False
        log.debug("浮窗%s", "展开" if self.expanded else "收起")
        self._place_body()

    def _click_toggle(self):
        self.toggle()
        self.give_back_focus()

    def give_back_focus(self):
        # 等这次点击处理完再还焦点，否则 Windows 会在点击结束时又把浮窗激活。
        # 只在前台确实是浮窗（打字中，或云控退回 1.1.8 时点了就激活）时还；前台本来就在游戏、玩家切到了别的程序，什么也不做
        self._typing_on = False
        self._no_activate()
        target = self.target_hwnd

        def back():
            if target and target == self.target_hwnd and win.foreground_window() in self._panel_hwnds():
                win.give_focus(target)
        QTimer.singleShot(0, back)

    def eventFilter(self, obj, ev):
        if obj is self.body_win and ev.type() == QEvent.WindowDeactivate and self._typing_on:
            QTimer.singleShot(0, self._sync_no_activate)  # 等 Windows 换完前台再看是不是点到别处了
        if obj is self.search and ev.type() == QEvent.MouseButtonPress:
            self._start_typing()  # 点进搜索框才让详情窗口当前台，不然字打不进来
        if obj is self.search and ev.type() == QEvent.KeyPress and ev.key() == Qt.Key_Escape:
            self.search.clear()
            self.give_back_focus()
            return True
        return super().eventFilter(obj, ev)

    def _typing(self) -> bool:
        return self._typing_on and self.search.hasFocus()

    def auto_hide(self, phase, in_champ_select: bool = True) -> None:
        """展开后鼠标离开一段时间就收起。选英雄阶段自动展开的不收（进了游戏才开始计时）。"""
        if not self.expanded or not self.isVisible():
            return
        pos = QCursor.pos()
        if self.frameGeometry().contains(pos) or (self.body_win.isVisible() and self.body_win.frameGeometry().contains(pos)):
            self._hovered, self._away_since = True, None
            return
        # 选英雄阶段自动展开的：一直开着，换英雄时内容跟着换；只有手动点图标收起，进游戏后再按时收起
        # 选英雄阶段秒退回大厅（还是 client 但不在选英雄界面）：照常计时收起，不一直盖在客户端右上角
        # 进游戏没认出英雄自动展开的：一直开着提示手动选，直到选好英雄（update_from 里清掉）或玩家点图标收起。
        # 以前 5 秒就收起，那时多半还在读条，玩家看不到；1.1.2 日志里约 3% 的局整局按全英雄数据推荐
        if self._typing() or (self._auto_opened and phase == "client" and in_champ_select) or self._hold_for_hero:
            self._away_since = None
            return
        now = time.monotonic()
        if self._away_since is None:
            self._away_since = now
        elif now - self._away_since >= AUTO_HIDE_SECONDS:
            log.debug("浮窗：鼠标离开 %d 秒，自动收起（%s）", AUTO_HIDE_SECONDS, phase)
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
            self.search_feedback.hide()
            self.give_back_focus()

    def _search_pick(self):
        t = self.search.text().strip()
        hid = self.engine.store.resolve_hero(self._names.get(t, t)) if t else None
        if not hid:
            self.search_feedback.show()
            return
        self.engine.set_hero(hid)
        self.search.clear()
        self.search_feedback.hide()
        self.give_back_focus()

    def _link(self, href):
        if href == "rescan":
            self.engine.rescan()
            self.give_back_focus()
        elif href == "quit":
            from .mainwindow import quit_app
            quit_app(self.engine)

    def set_bg(self, level: str, shown: bool = True) -> None:
        """详情的背景（见 Overlay.apply_bg）。shown：详情里显示「背景」按钮。"""
        self.bg_level = level
        self.body.setStyleSheet(self.STYLE_BODY % self.PANEL_BG.get(level, self.PANEL_BG["standard"]))
        self._show_bg_btn()
        self.bg_btn.setVisible(shown)

    def _show_bg_btn(self) -> None:
        self.bg_btn.setText("背景：" + dict(OVERLAY_BGS).get(self.bg_level, "透明"))

    def _next_bg(self) -> None:
        codes = [code for code, _ in OVERLAY_BGS]
        self.bg_picked.emit(codes[(codes.index(self.bg_level) + 1) % len(codes)] if self.bg_level in codes else codes[0])

    def _set_btn_size(self, d: int) -> None:
        """图标按钮直径（逻辑像素）。"""
        if self.btn.width() != d:
            self.btn.setFixedSize(d, d)
            self.btn.setIconSize(QSize(round(d * 0.75), round(d * 0.75)))
            self.btn.setStyleSheet(self.BTN_STYLE % (d // 2))

    def _game_position(self, rect, width: int, scale: float) -> tuple[int, int]:
        x = rect.right - width - round(12 * scale) if self.corner == "top-right" else rect.left + round(12 * scale)
        return x, rect.top + round(90 * scale)

    def button_needed(self, snap: dict, rect, foreground: bool) -> bool:
        """按需模式下的游戏按钮。只查鼠标位置，不用透明窗口占着热区。

        热区和游戏内按钮使用同一套物理像素坐标，边缘多留 8 个逻辑像素；
        离开后留一点时间供点击。详情或三选一正在显示时不隐藏按钮。
        """
        target = (rect, self.corner)
        if target != self._reveal_target or not foreground:
            self._reveal_target = target
            self._reveal_until = 0.0
        if not foreground:
            return False
        if self.expanded or (snap.get("offer") and snap.get("evaluation")) or self.pinned():
            return True
        k = self.devicePixelRatioF() or 1.0
        size, pad = round(GAME_BUTTON * k), round(REVEAL_PAD * k)
        x, y = self._game_position(rect, size, k)
        if win.IS_WINDOWS:
            cx, cy = win.cursor_pos()
        else:
            pos = QCursor.pos()
            cx, cy = round(pos.x() * k), round(pos.y() * k)
        now = time.monotonic()
        if x - pad <= cx < x + size + pad and y - pad <= cy < y + size + pad:
            self._reveal_until = now + BUTTON_REVEAL_SECONDS
        return now < self._reveal_until

    def place(self, rect, in_client: bool = False) -> None:
        """rect：游戏或客户端窗口（物理像素）。在客户端（大厅、选英雄）且浮窗在右上角时，
        图标放在头像右边「在线」那一行、铃铛左边，大小跟着客户端窗口走；进游戏后和以前一样贴在右上角。"""
        self._sync_no_activate()
        badge = client_badge(rect) if in_client and rect is not None and self.corner == "top-right" else None
        k = self.devicePixelRatioF() or 1.0
        self._set_btn_size(max(20, round(badge[2] / k)) if badge else GAME_BUTTON)
        self.adjustSize()
        if rect is not None and win.IS_WINDOWS:
            # 和标注层一样按物理像素摆
            hwnd = int(self.winId())
            cur = win.window_rect(hwnd)
            if cur:
                if badge:  # 图标（浮窗最上面靠右）的中心对准算好的位置
                    bw = round(self.btn.width() * k)
                    x, y = badge[0] + bw // 2 - cur.width, badge[1] - bw // 2
                else:
                    x, y = self._game_position(rect, cur.width, k)
                key = (_rect_text(rect), in_client, x, y, cur.width, cur.height)
                if key != getattr(self, "_place_logged", None):
                    self._place_logged = key
                    log.debug("浮窗摆放：%s 窗口 %s，图标 %s，浮窗现在 %s → 摆到 %d,%d（%s，缩放 %g）",
                              "客户端" if in_client else "游戏", _rect_text(rect), badge, _rect_text(cur), x, y,
                              "展开" if self.expanded else "收起", k)
                if (cur.left, cur.top) != (x, y):
                    win.set_window_rect(hwnd, win.Rect(x, y, cur.width, cur.height))
                    self._publish()  # SetWindowPos 不一定触发 Qt 的 moveEvent
                self._place_body()
                return
        if badge:
            bw = self.btn.width()
            self.move(_logical(badge[0]) + bw // 2 - self.width(), _logical(badge[1]) - bw // 2)
            self._place_body()
            return
        if rect is not None:
            left, top, right = _logical(rect.left), _logical(rect.top), _logical(rect.right)
        else:
            g = QGuiApplication.primaryScreen().availableGeometry()
            left, top, right = g.left(), g.top(), g.right()
        x = right - self.width() - 12 if self.corner == "top-right" else left + 12
        self.move(x, top + 90)
        self._place_body()

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

    def pinned(self) -> bool:
        """对局开始到第一次海克斯选完（再过半秒）：按钮固定显示。"""
        return self._ingame_since is not None and not self._pin_over

    def _first_offer_pin(self, snap: dict) -> None:
        """进对局后按钮固定显示；第一次海克斯选择结束半秒后，按钮连同详情直接隐藏。"""
        if snap.get("phase") != "ingame":
            self._offer_seen, self._ingame_since, self._unpin_at, self._pin_over = False, None, None, False
            return
        now = time.monotonic()
        if self._ingame_since is None:
            self._ingame_since = now
        if self._pin_over:
            return
        if snap.get("offer") and snap.get("evaluation"):
            self._offer_seen = True
            return
        if self._offer_seen and self._unpin_at is None:
            self._unpin_at = now + FIRST_OFFER_HIDE_SECONDS
        if self._unpin_at is not None:
            if now < self._unpin_at:
                return
            why = "第一次海克斯选择结束"
        elif now - self._ingame_since >= PIN_MAX_SECONDS:
            why = f"进对局 {PIN_MAX_SECONDS} 秒还没等到海克斯选择"
        else:
            return
        self._pin_over = True
        # 详情开着也一起收起（按需模式下按钮跟着隐藏）；没认出英雄、正在搜英雄、鼠标在浮窗上时不收
        pos = QCursor.pos()
        on_panel = self.frameGeometry().contains(pos) or (
            self.body_win.isVisible() and self.body_win.frameGeometry().contains(pos))
        if self.expanded and not self._hold_for_hero and not self._typing() and not on_panel:
            self.toggle()
        log.info("%s，游戏按钮不再固定显示", why)

    def update_from(self, snap: dict) -> None:
        # 选英雄阶段识别到新英雄时自动展开一次，方便看胜率、出装、加点
        hid = snap.get("hero_id")
        if snap.get("phase") != "ingame":
            self._missing_hero_notified = False
        elif not hid and not self._missing_hero_notified:
            self._missing_hero_notified = True
            if not self.expanded:
                self.toggle()
            self._hold_for_hero = True  # 本来就开着的也要留住，不然几秒后按时收起
            log.info("未识别到英雄，请手动选择")
        if hid or snap.get("phase") != "ingame":
            self._hold_for_hero = False  # 选好了（手动或补认出）、对局结束：照常按时收起
        if not hid:
            self._auto_opened_for = None  # 英雄清掉了（新的一把）：下一把同一个英雄也再自动展开
        self._first_offer_pin(snap)
        if hid and snap.get("phase") == "client" and self._auto_opened_for != hid:
            self._auto_opened_for = hid
            if not self.expanded:
                log.debug("选英雄：认出英雄 %s，浮窗自动展开", hid)
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
            head = self._with_hide_hint(h['name'], h['nick'], snap)
            hero_html = head + ("" if head.startswith("<table") else "<br>") + ' · '.join(rating)
        else:
            hero_html = self._with_hide_hint(None, None, snap)
        if self.hero_label.text() != hero_html:
            self.hero_label.setText(hero_html)
        self._set_candidates(snap.get("candidates") or [])

        parts = []
        cs = snap.get("champ_select")
        if cs and (cs.get("bench") or cs.get("cards")):
            parts.append(champ_select_summary(cs, show_win))
        ev = snap.get("evaluation")
        if ev:
            waiting = any((snap.get("offer") or {}).get("pending_columns") or ())
            advice = "卡片更新中，等待识别确认" if waiting else ev['advice']
            parts.append(f"<b>{advice}</b>")
            pk = recommend.pentakill_text(ev.get("pentakill")) if not waiting else ""
            if pk:
                parts.append(f"<span style='color:#c9a0ff'>{escape(pk)}</span>")
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
            parts.append("<span style='color:#ff7a6b'>游戏可能是全屏模式，无法识别。请在游戏内设置-窗口模式中选择无边框/窗口。</span>")
        parts.append(f"<span style='color:#8b93a7'>{data_status_html(snap.get('data', {}))}</span>")
        parts.append("<a href='rescan' style='color:#62a8ff'>重新识别</a>　"
                     "<a href='quit' style='color:#ff7a6b'>退出助手</a>")
        html = "<br>".join(parts)
        if self.info.text() != html:
            self.info.setText(html)
            self._place_body()  # 详情变高：往下长，位置不变

    HIDE_HINT = "收起后按钮隐藏，鼠标移过来浮现 ↗"
    HIDE_HINT_LEFT = "收起后按钮隐藏，鼠标移到左上角浮现"

    def _with_hide_hint(self, name: str | None, nick: str | None, snap: dict) -> str:
        """英雄名那一行（name 为 None：没认出英雄）。对局中按需模式（收起后按钮会藏起来）在同一行最右边写一句怎么叫出来；
        不放「退出助手」旁边，免得当成退出后还能唤出。按钮在右上角时紧挨按钮，在左上角时写明位置。
        名字加称号太长（只有「烈娜塔 · 戈拉斯克 炼金男爵」）就不写称号，免得一行挤不下折断。"""
        on = snap.get("phase") == "ingame" and snap.get("settings", {}).get("overlay_on_demand", False) is True
        if name is None:
            first = "<span style='color:#e7c35a'>未识别到英雄，请手动选择</span>"
        else:
            if on and len(name) + len(nick or "") > 11:
                nick = ""
            first = f"<b style='color:#e7c35a;font-size:14px'>{name}</b> {nick or ''}".rstrip()
        if not on:
            return first
        hint = self.HIDE_HINT if self.corner == "top-right" else self.HIDE_HINT_LEFT
        return ("<table width='100%' cellspacing='0' cellpadding='0'><tr>"
                f"<td style='vertical-align:middle'>{first}</td>"
                f"<td align='right' style='vertical-align:middle;padding-left:6px'>"
                f"<p style='white-space:nowrap;color:#aab3c7;font-size:12px'>{hint}</p></td>"
                "</tr></table>")


class Overlay:
    def __init__(self, engine):
        self.engine = engine
        win.watch_foreground()
        self.badges = BadgeLayer()
        self.badges.icons = engine.icons
        self.champ = ChampSelectLayer()
        self.panel = ControlPanel(engine, engine.settings.overlay_corner)
        self.visible = engine.settings.overlay
        self._bg = None
        self.apply_bg()
        self.hotkey = GameHotkey()
        self.hotkey.activated.connect(self._toggle_details)
        self.hotkey.sync(engine.settings.overlay_hotkey, False)
        self.autoshot = AutoShot(engine)  # 设置里开了自动截图时，三选一和选英雄时自动截整屏
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(250)

    def set_visible(self, on: bool):
        self.visible = on
        if on:
            self._tick()
        else:
            self.hotkey.sync(self.engine.settings.overlay_hotkey, False)
            self.panel.hide()
            self.badges.hide()
            self.champ.hide()

    def apply_bg(self) -> None:
        """详情的背景（详情里的「背景」按钮，1.2.0）：透明（1.1.9 的样子）/ 半透明 / 不透明。
        只改右上角详情面板，三选一标签、选英雄标注不变。云控 features.overlay_bg=false 退回 1.1.9：
        按透明画、详情里不显示按钮，玩家选的留着。"""
        on = cloud.feature("overlay_bg")
        level = self.engine.settings.overlay_bg if on else "standard"
        if (level, on) == self._bg:
            return
        self._bg = (level, on)
        log.info("详情背景 %s（云控开关 overlay_bg=%s）", level, "开" if on else "关")
        self.panel.set_bg(level, shown=on)

    def _toggle_details(self) -> None:
        """处理按键时重新核对前台，丢弃切出游戏前排队的消息。"""
        if not self.visible:
            return
        snap = self.engine.snapshot()
        game = win.find_game_window()
        target = game or win.find_client_window()
        playing = snap.get("phase") == "ingame" if game else (
            snap.get("phase") == "client" and bool(snap.get("champ_select")))
        if (not playing or not target or not win.client_rect(target) or not win.is_foreground(target)
                or self._panel_typing() or not cloud.feature("overlay_hotkey", False)):
            self.hotkey.sync(self.engine.settings.overlay_hotkey, False)
            return
        self.panel.toggle()
        self._tick()  # 按需模式下按钮可能完全隐藏：展开后立即摆放并显示详情。

    def _panel_typing(self) -> bool:
        """1.2.0：前台是详情或补全列表（玩家在搜英雄）。这时快捷键不注册、排队的按键也不处理，免得打字时收起详情。
        云控 features.panel_typing_in_game=false 退回 1.1.9（前台是详情也按游戏在前台算）。"""
        return cloud.feature("panel_typing_in_game") and self.panel.owns_foreground()

    def _tick(self):
        crashlog.enforce_limit()
        if not getattr(self, "visible", True):
            self.hotkey.sync(self.engine.settings.overlay_hotkey, False)
            return
        snap = self.engine.snapshot()
        if snap.get("vision", {}).get("blank") and not getattr(self, "_blank_warned", False):
            self._blank_warned = True  # 每次打开软件最多提醒一次
            notify = getattr(self, "notify", None)
            if notify:
                notify("游戏可能是全屏模式，无法识别。请在游戏内设置-窗口模式中选择无边框/窗口。")
        game = win.find_game_window()
        grect = win.client_rect(game) if game else None
        game_foreground = bool(grect and win.is_foreground(game))
        # 1.2.0：从游戏点进详情搜英雄时前台是详情窗口（或补全列表），游戏还在后面，按游戏在前台画、不藏浮窗。
        # 1.1.9 可能当成离开了游戏，按需模式把浮窗藏掉，输入框焦点跟着丢，没认出英雄时手动选不了。
        # 从盖在游戏上的别的程序点进来的不算，标签照旧不画到那个程序上。打字期间只看点进来之前是谁在前台，
        # 不再看 is_foreground：前台监听没装上时它记的可能还是游戏；不知道从哪来（点之前详情已在前台）才照旧看它。
        # 云控 features.panel_typing_in_game=false 退回 1.1.9
        source = self.panel.typing_source() if cloud.feature("panel_typing_in_game") else None
        over_game = game_foreground if source is None else bool(grect) and source == game
        self.badges.update_from(snap, grect if over_game else None, game)
        client = None if grect else win.find_client_window()
        crect = win.client_rect(client) if client else None
        # 1.2.0 起不注册详情快捷键（不好按，只用鼠标唤出）；云控 features.overlay_hotkey=true 退回 1.1.9
        typing = self._panel_typing()
        hotkey_active = cloud.feature("overlay_hotkey", False) and not typing and (
            (snap.get("phase") == "ingame" and game_foreground) or (
                snap.get("phase") == "client" and bool(snap.get("champ_select"))
                and bool(crect and win.is_foreground(client))))
        self.hotkey.sync(self.engine.settings.overlay_hotkey, bool(hotkey_active))
        # 标注层置顶：客户端不在前台（切到别的窗口）时隐藏，免得盖在别的程序上
        self.champ.update_from(snap, crect if crect and win.is_foreground(client) else None, client or 0)
        self.autoshot.tick(snap, grect, crect if crect and win.is_foreground(client) else None)
        self.panel.target_hwnd = game or client or 0
        self.panel.update_from(snap)
        self.panel.auto_hide(snap.get("phase"), bool(snap.get("champ_select")))
        # 选英雄阶段跟随客户端窗口，进游戏后跟随游戏窗口；两者都不在时不显示浮窗（用托盘和主界面）
        rect = grect or crect
        if rect is None:
            if self.panel.isVisible():
                log.debug("浮窗：游戏和客户端窗口都找不到，隐藏")
            self.panel.hide()
            return
        on_demand = snap.get("settings", {}).get("overlay_on_demand", False) is True
        if on_demand and grect and snap.get("phase") == "ingame":
            if not self.panel.isVisible():
                # 隐藏时也跟随目标显示器，让 Qt 收到 DPI 变化；不先 show，免得每轮闪一下。
                self.panel.place(grect)
            if not self.panel.button_needed(snap, grect, over_game):
                if self.panel.isVisible():
                    if self.panel.expanded:  # 开着的详情被藏掉：记下当时谁在前台，好查焦点问题
                        log.info("浮窗：游戏不在前台，藏起开着的详情（前台 %s）", win.describe(win.foreground_window()))
                    self.panel.hide()
                return
        if not self.panel.isVisible():
            log.debug("浮窗：显示（%s）", "游戏" if grect else "客户端")
            self.panel.show()
        self.panel.place(rect, in_client=grect is None)


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

    from . import theme
    from .mainwindow import MainWindow, Tray, app_icon

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("海克斯大乱斗助手")
    app.setWindowIcon(app_icon())
    theme.apply(app)

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
    from ..config import app_home
    if cloud.feature("crash_log"):  # 确认是唯一实例后再开：第二个实例不能清掉正在用的记录
        crashlog.enable(app_home())

    engine = make_engine()
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
            conn.disconnectFromServer()
            conn.deleteLater()  # 父对象是 server：不删的话每次双击图标留一个连接
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
            QMessageBox.information(main_win, "使用前", "请在游戏内设置-窗口模式中选择无边框/窗口。\n\n"
                                    "全屏模式下截不到画面，识别不了。")
        QTimer.singleShot(1200, welcome)
    failed = patch.take_failure()
    if failed:
        incomplete = patch.rollback_incomplete()
        log.warning("上次更新没装上%s：%s", "，旧文件没有全部还原" if incomplete else "", failed)
        tray.showMessage("海克斯大乱斗助手",
                         "上次更新没有装上，也没能完全恢复原版本。请到官网重新下载安装。" if incomplete
                         else "上次更新没有装上，已恢复原版本。打开主界面点「立即更新」再试一次。",
                         app_icon(), 10000)
    main_win.check_update()
    main_win.refresh_rank_if_stale()
    if not minimized:
        main_win.show_and_raise()
    app._keep = (ov, main_win, tray, server)  # 保持引用
    return app.exec()
