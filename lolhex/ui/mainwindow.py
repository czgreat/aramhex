"""主界面与系统托盘。

- 主窗口：数据状态、当前英雄（识别结果/候选/搜索手动选）、英雄评级与出装加点、当前三选一推荐、设置。
- 关闭主窗口只是隐藏到托盘；退出要在托盘菜单里点「退出」。
- 托盘：双击打开主界面；右键菜单。
"""

from __future__ import annotations

import logging
import os
import random
import threading
import webbrowser
from html import escape
from importlib import resources

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar, QScrollArea,
                               QPushButton, QSizePolicy, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget)

from .. import DATA_HOMEPAGE, HOMEPAGE, __version__, cloud, cloudsettings, i18n, langpack, logupload, patch, rank, recommend, update
from ..config import SPEEDS, is_portable
from ..vision import window as win
from ..data.public import BANDS, norm_band
from . import autostart
from .badgepos import POSITIONS as BADGE_POSITIONS, BadgePosPreview
from . import buildview
from .data_status import data_status_html
from .herosearch import hero_completer

RARITY_CN = {"silver": "白银", "gold": "黄金", "prismatic": "棱彩"}
# 云控强制设置时提示用的名字
SETTING_NAMES = {"show_winrate": "显示胜率数字", "overlay": "显示游戏内浮窗", "overlay_corner": "浮窗位置",
                 "badge_position": "三选一标签", "speed": "识别频率",
                 "check_data_updates": "自动获取最新海克斯数据", "game_lang": "游戏语言", "data_band": "参考分数段"}
VERDICT_CN = {"best": ("首选", "#2e9d5b"), "ok": ("可选", "#3b7dd8"), "reroll": ("可考虑刷新", "#d0533f"),
              "unknown": ("未知", "#888"), "nodata": ("无数据", "#888")}
PHASE_CN = {None: "未检测到英雄联盟", "client": "客户端（选英雄界面会自动识别英雄）", "ingame": "对局中"}
VISION_CN = {"idle": "待机", "paused": "游戏不在前台，暂停", "scanning": "扫描中"}


def app_icon() -> QIcon:
    data = resources.files("lolhex.ui").joinpath("icon.png").read_bytes()
    pm = QPixmap()
    pm.loadFromData(data)
    return QIcon(pm)


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _progress_bar() -> QProgressBar:
    bar = QProgressBar()
    bar.setTextVisible(True)
    bar.setMaximumHeight(18)
    bar.hide()
    return bar


def show_progress(bar: QProgressBar, label: str, done: int | None = None, total: int | None = None) -> None:
    """进度条上写"<在做什么> 已下载 / 总共 MB"；不知道总量时只写文字。"""
    if total:
        bar.setRange(0, 1000)
        bar.setValue(int(1000 * min(done or 0, total) / total))
        bar.setFormat(f"{label} {(done or 0) / 1e6:.1f} / {total / 1e6:.1f} MB")
    else:
        bar.setRange(0, 1)
        bar.setValue(0)
        bar.setFormat(label)
    bar.show()


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    f.setStyleSheet("QFrame#card{border:1px solid #d7dbe3;border-radius:6px;background:#fbfcfe}")
    return f


def hard_exit_later(seconds: float = 5.0, reason: str = "退出超时，强制结束以便更新") -> None:
    """更新要替换文件：正常退出卡住（后台线程、网络请求没结束）时，几秒后强制结束本进程。
    1.0.9 之前在 172 上出现过：旧助手没退出，文件被占用，更新被还原。"""
    import threading

    def go():
        logging.getLogger(__name__).warning(reason)
        logging.shutdown()
        os._exit(0)
    t = threading.Timer(seconds, go)
    t.daemon = True
    t.start()


QUIT_TIMEOUT_S = 3.0


def quit_app(engine) -> None:
    """托盘「退出」和浮窗「退出」：停后台线程、结束事件循环；几秒内没退干净就强制结束进程。
    1.0.91 在 172 上出现过：点了退出，后台线程都停了，界面事件循环却没结束，进程一直留着、文件删不掉。"""
    logging.getLogger(__name__).info("退出")
    app = QApplication.instance()
    if app is not None:
        app.setProperty("quitting", True)  # 主窗口的关闭不再改成缩到托盘
    engine.stop()
    hard_exit_later(QUIT_TIMEOUT_S, "退出超时，强制结束")
    QApplication.exit(0)


class RankWorker(QThread):
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, riot_id: str, parent=None):
        super().__init__(parent)
        self.riot_id = riot_id

    def run(self):
        try:
            self.done.emit(rank.lookup(self.riot_id))
        except rank.RankError as e:
            self.failed.emit(str(e))
        except Exception as e:
            logging.getLogger(__name__).warning("查分出错：%s", e)
            self.failed.emit("查询出错，请稍后再试")


class UpdateWorker(QThread):
    """后台线程：mode=check 查有没有新版本（手动检查时先同步统计数据，结果发 data_done）；
    mode=download 先试增量更新（只下变了的文件，官网优先），不行再从 GitHub 下完整安装包；
    增量失败、安装包下不了或太慢、便携版要整包，都发 manual（原因, 网盘链接）让用户去网盘下载。
    strict=True（手动检查）时连不上会发 failed，没有新版本发 uptodate。"""
    found = Signal(dict)
    uptodate = Signal()
    progress = Signal(int, int)
    ready = Signal(str)
    patched = Signal(list, list)
    phase = Signal(str)  # 当前在做什么（检查更新文件 / 比对本机文件 / 下载更新 / 下载安装包）
    failed = Signal(str)
    data_done = Signal(str)
    manual = Signal(str, dict)
    cloud = Signal(dict)

    def __init__(self, mode: str, info: dict | None = None, parent=None, strict: bool = False, data_sync=None):
        super().__init__(parent)
        self.mode, self.info, self.strict, self.data_sync = mode, info, strict, data_sync

    def run(self):
        try:
            if self.mode == "check":
                self.cloud.emit(cloud.refresh())
                if self.data_sync:
                    try:
                        self.data_done.emit(self.data_sync())
                    except Exception as e:
                        logging.getLogger(__name__).warning("手动同步统计数据失败：%s", e)
                        self.data_done.emit("统计数据这次没连上，稍后会自动再试。")
                info = update.check(__version__, strict=self.strict)
                if info:
                    self.found.emit(info)
                else:
                    self.uptodate.emit()
            else:
                log = logging.getLogger(__name__)
                try:
                    changed, stale = patch.prepare(self.info["version"], self.progress.emit, phase=self.phase.emit)
                    self.patched.emit(changed, stale)
                    return
                except patch.NeedFullInstall as e:
                    log.info("要整包更新：%s", e)
                    reason = "这次更新需要重新下载安装包。"
                except Exception as e:
                    log.warning("增量更新失败：%s", e)
                    self.manual.emit("更新文件下载失败。", update.pan_links())
                    return
                if not self.info.get("url") or is_portable():
                    self.manual.emit(reason, update.pan_links())
                    return
                self.phase.emit("从 GitHub 下载安装包…")
                try:
                    self.ready.emit(str(update.download(self.info, self.progress.emit)))
                except update.SlowDownload as e:
                    log.info("GitHub %s", e)
                    self.manual.emit("从 GitHub 下载太慢。", update.pan_links())
                except Exception as e:
                    log.warning("从 GitHub 下载安装包失败：%s", e)
                    self.manual.emit("从 GitHub 下载安装包失败。", update.pan_links())
        except Exception as e:  # 更新失败不能影响助手本身
            self.failed.emit(str(e))


BAND_CHOICES = [("", "全体")] + [(c, BANDS[c]) for c in BANDS]


def about_html() -> str:
    links = [f"<a href='{cloud.site()}'>官网</a>", f"<a href='{HOMEPAGE}'>GitHub</a>", f"<a href='{DATA_HOMEPAGE}'>统计数据</a>"]
    for c in cloud.community():  # 云控：QQ 群、B 站、赞助……
        label = escape(c["name"] + (" " + c["text"] if c.get("text") else ""))
        links.append(f"<a href='{escape(c['url'])}'>{label}</a>" if c.get("url") else label)
    return (f"<b>AramHex 海克斯大乱斗助手</b> {__version__}{'（便携版）' if is_portable() else ''}<br>" + " · ".join(links) +
            "<br><span style='color:#777'>非官方工具，与腾讯、Riot Games 无关。</span>")


class MainWindow(QMainWindow):
    def __init__(self, engine, overlay):
        super().__init__()
        self.engine = engine
        self.overlay = overlay
        self._names: dict[str, str] = {}
        self._cand_ids: list = []
        self._panel_hero = None
        self._hint_shown = False
        self.setWindowTitle(f"海克斯大乱斗助手 v{__version__}")
        self.setWindowIcon(app_icon())
        self.resize(700, 820)
        self.setMinimumSize(360, 260)

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        # 顶部状态
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(40, 40))
        title = QLabel("<b style='font-size:17px'>海克斯大乱斗助手</b> "
                       f"<span style='font-size:13px;color:#555'>{__version__}</span>")
        head.addWidget(logo)
        head.addWidget(title, 1)
        self.btn_comm = QPushButton()  # 云控里标了 primary 的社区入口（比如 QQ 群），没有就不显示
        self.btn_comm.clicked.connect(self._open_primary)
        self.btn_comm.hide()
        head.addWidget(self.btn_comm)
        self.btn_dash = QPushButton("帮助")
        self.btn_dash.clicked.connect(lambda: webbrowser.open(f"{cloud.site()}/#faq"))
        head.addWidget(self.btn_dash)
        self._show_primary()
        lay.addLayout(head)

        # 有新版本时才显示
        self.update_bar = _card()
        self.update_bar.setStyleSheet("QFrame#card{border:1px solid #e0b64a;border-radius:6px;background:#fff8e1}")
        ubv = QVBoxLayout(self.update_bar)
        ub = QHBoxLayout()
        ubv.addLayout(ub)
        self.update_text = QLabel()
        self.update_text.setWordWrap(True)
        self.btn_update = QPushButton("立即更新")
        self.btn_update.clicked.connect(self._start_update)
        ub.addWidget(self.update_text, 1)
        ub.addWidget(self.btn_update)
        self.update_progress = _progress_bar()  # 更新时显示：在做什么、下了多少
        ubv.addWidget(self.update_progress)
        self.update_bar.hide()
        lay.addWidget(self.update_bar)
        self._update_info = None
        self._worker = None
        # 助手常驻托盘，可能好几天不重启：平时也定时查更新
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self.check_update)
        self._update_timer.start(self._check_interval_ms())
        # 提示和弹窗都不在对局中出现（会抢游戏的焦点）：对局中先攒着，回到游戏外再出
        self._deferred_cloud: dict | None = None
        self._popup_open = False  # 有提醒类弹窗开着（不叠第二个）
        self._deferred_toast: str | None = None
        self._shown_note = None
        self._asked_offers: set = set()
        self._notify_timer = QTimer(self)
        self._notify_timer.timeout.connect(self._notify_tick)
        self._notify_timer.start(3000)

        self.status = QLabel()
        self.status.setWordWrap(False)  # 内容自带 <br> 换行；自动换行会让高度算不准被压扁
        self.status.setTextFormat(Qt.RichText)
        sc = _card()
        scl = QVBoxLayout(sc)
        scl.addWidget(self.status)
        self.data_progress = _progress_bar()  # 下载统计数据时显示
        scl.addWidget(self.data_progress)
        lay.addWidget(sc)

        # 英雄
        hc = _card()
        hl = QVBoxLayout(hc)
        self.hero_label = QLabel()
        self.hero_label.setTextFormat(Qt.RichText)
        hl.addWidget(self.hero_label)
        self.cs_label = QLabel()  # 选人界面：待选栏按胜率排序的汇总
        self.cs_label.setTextFormat(Qt.RichText)
        self.cs_label.setWordWrap(True)
        self.cs_label.hide()
        hl.addWidget(self.cs_label)
        self.cands = QHBoxLayout()
        hl.addLayout(self.cands)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索英雄")
        self.search.returnPressed.connect(self._search_pick)
        btn_clear = QPushButton("清除")
        btn_clear.clicked.connect(lambda: self.engine.set_hero(None))
        row.addWidget(self.search, 1)
        row.addWidget(btn_clear)
        hl.addLayout(row)
        brow = QHBoxLayout()
        brow.addWidget(QLabel("分段"))
        self.band_box = QComboBox()
        for code, label in BAND_CHOICES:
            self.band_box.addItem(label, code)
        self.band_box.setCurrentIndex(max(0, self.band_box.findData(engine.settings.data_band or "")))
        self.band_box.currentIndexChanged.connect(lambda i: self.engine.set_band(self.band_box.itemData(i) or None))
        brow.addWidget(self.band_box)
        self.my_rank_label = QLabel()
        self.my_rank_label.setStyleSheet("color:#555")
        brow.addWidget(self.my_rank_label)
        brow.addStretch(1)
        hl.addLayout(brow)
        lay.addWidget(hc)

        # 标签页
        self.tabs = QTabWidget()
        self.tab_hero = QLabel()
        self.tab_hero.setTextFormat(Qt.RichText)
        self.tab_hero.setWordWrap(True)
        self.tab_hero.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.tab_hero.setTextInteractionFlags(Qt.TextSelectableByMouse)
        w1 = QWidget()
        QVBoxLayout(w1).addWidget(self.tab_hero)
        # 出装、加点很长：放进滚动区，不然标签页按最高的一页定高度，选了英雄后设置页的「检查更新」被挤出窗口
        w1_scroll = QScrollArea()
        w1_scroll.setWidget(w1)
        w1_scroll.setWidgetResizable(True)
        w1_scroll.setFrameShape(QFrame.NoFrame)
        w1_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.tabs.addTab(w1_scroll, "出装与加点")

        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        self.offer_advice = QLabel("未在选海克斯")
        self.offer_advice.setWordWrap(True)
        l2.addWidget(self.offer_advice)
        grid = QGridLayout()
        self.offer_cards = []
        for i in range(3):
            lab = QLabel()
            lab.setTextFormat(Qt.RichText)
            lab.setWordWrap(True)
            lab.setAlignment(Qt.AlignTop)
            lab.setMinimumWidth(170)
            fr = _card()
            QVBoxLayout(fr).addWidget(lab)
            grid.addWidget(fr, 0, i)
            self.offer_cards.append(lab)
        l2.addLayout(grid)
        btns = QHBoxLayout()
        b1 = QPushButton("重新识别")
        b1.clicked.connect(self.engine.rescan)
        b2 = QPushButton("重新校准卡片位置")
        b2.clicked.connect(self._recalibrate)
        btns.addWidget(b1)
        btns.addWidget(b2)
        btns.addStretch(1)
        l2.addLayout(btns)
        l2.addStretch(1)
        self.tabs.addTab(w2, "海克斯三选一")

        self.tabs.addTab(self._settings_tab(), "设置")
        lay.addWidget(self.tabs, 1)
        # 小屏或高缩放时保留完整内容，允许滚动，不让底部操作落到屏幕外。
        root.setMinimumWidth(640)
        viewport = QScrollArea()
        viewport.setWidgetResizable(True)
        viewport.setFrameShape(QScrollArea.NoFrame)
        viewport.setWidget(root)
        self.setCentralWidget(viewport)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    # ---------- 设置 ----------

    def _settings_tab(self) -> QWidget:
        s = self.engine.settings
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setSpacing(14)
        f = QFormLayout()
        f.setVerticalSpacing(10)
        outer.addLayout(f)
        self.set_corner = QComboBox()
        self.set_corner.addItems(["右上角", "左上角"])
        self.set_corner.setCurrentIndex(0 if s.overlay_corner == "top-right" else 1)
        # 三选一标签放卡片上方还是下方，旁边的示意图跟着选项变
        self.set_badge = QComboBox()
        for code, label in BADGE_POSITIONS:
            self.set_badge.addItem(label, code)
        self.set_badge.setCurrentIndex(max(0, self.set_badge.findData(s.badge_position)))
        self.badge_preview = BadgePosPreview(s.badge_position)
        self.set_badge.currentIndexChanged.connect(
            lambda i: self.badge_preview.set_position(self.set_badge.itemData(i)))
        # 示意图比下拉框高：右边放示意图，左边下拉框下面接着放后面几项，不留空白
        badge_row = QHBoxLayout()
        badge_left = QVBoxLayout()
        badge_left.setSpacing(10)
        badge_row.addLayout(badge_left, 1)
        badge_row.addWidget(self.badge_preview, 0, Qt.AlignTop)
        self.set_speed = QComboBox()
        for key, (label, _) in SPEEDS.items():
            self.set_speed.addItem(label, key)
        self.set_speed.setCurrentIndex(max(0, self.set_speed.findData(s.speed)))
        self.set_auto = QCheckBox("开机自动启动")
        self.set_auto.setChecked(autostart.is_enabled())
        self.set_data = QCheckBox("自动获取最新海克斯数据（建议开启）")
        self.set_data.setChecked(s.check_data_updates)
        self.set_usage = QCheckBox("帮助改善软件（匿名）")
        self.set_usage.setChecked(s.send_usage)
        self.set_usage.setToolTip("开着时：联网时带一个随机编号（和账号、硬件无关），用来统计有多少人在用；\n"
                                  "排查问题时开发者可能请这台电脑上传运行日志，上传前去掉游戏账号名和电脑用户名。\n"
                                  "关掉就都不发。")
        # 外服：游戏客户端的语言。韩文要另外下载识别数据（安装包里不带）
        self.set_lang = QComboBox()
        for code, label in i18n.LANGS:
            self.set_lang.addItem(label, code)
        self.set_lang.setCurrentIndex(max(0, self.set_lang.findData(i18n.normalize_lang(s.game_lang))))
        self.lang_hint = QLabel()
        self.lang_hint.setStyleSheet("color:#555")
        self.btn_langpack = QPushButton(f"下载韩服识别数据（约 {langpack.KOREAN['size'] / 2 ** 20:.0f}MB）")
        self.btn_langpack.clicked.connect(self._download_langpack)
        lang_row = QHBoxLayout()
        lang_row.addWidget(self.lang_hint, 1)
        lang_row.addWidget(self.btn_langpack)
        self._langpack_state: dict | None = None  # 下载中 {done, total}；失败 {error}
        self._langpack_timer = QTimer(self)
        self._langpack_timer.timeout.connect(self._update_lang_hint)
        self.set_lang.currentIndexChanged.connect(self._update_lang_hint)
        self._update_lang_hint()
        f.addRow("游戏语言", self.set_lang)
        f.addRow("", lang_row)
        f.addRow("识别频率", self.set_speed)
        f.addRow("浮窗位置", self.set_corner)
        badge_left.addWidget(self.set_badge)
        badge_left.addWidget(self.set_auto)
        badge_left.addWidget(self.set_data)
        badge_left.addWidget(self.set_usage)
        save = QPushButton("保存设置")
        save.clicked.connect(self._save_settings)
        badge_left.addStretch(1)
        badge_left.addWidget(save)  # 放在示意图左边、和示意图底边对齐，不留空白
        f.addRow("三选一标签", badge_row)

        rk = _card()
        rv = QVBoxLayout(rk)
        rv.setContentsMargins(18, 14, 18, 14)
        rv.setSpacing(8)
        rv.addWidget(QLabel("<b>我的水平</b>"))
        rrow = QHBoxLayout()
        self.rank_input = QLineEdit(s.riot_id)
        self.rank_input.setPlaceholderText("名字#编号")
        self.rank_input.returnPressed.connect(self._query_rank)
        self.btn_rank = QPushButton("查询")
        self.btn_rank.setMinimumWidth(90)
        self.btn_rank.clicked.connect(self._query_rank)
        rrow.addWidget(self.rank_input, 1)
        rrow.addWidget(self.btn_rank)
        rv.addLayout(rrow)
        self.rank_result = QLabel()
        self.rank_result.setWordWrap(True)
        rv.addWidget(self.rank_result)
        outer.addWidget(rk)
        self._show_rank(s.my_rank)
        self._rank_worker = None
        outer.addStretch(1)
        # 版本和检查更新固定在页底，设置项较多时只滚动上面的表单。
        card = _card()
        about_row = QHBoxLayout(card)
        about_row.setContentsMargins(18, 12, 18, 12)
        cv = QVBoxLayout()
        cv.setSpacing(8)
        about_row.addLayout(cv, 1)
        about = QLabel(about_html())
        about.setTextFormat(Qt.RichText)
        about.setWordWrap(True)
        about.setOpenExternalLinks(True)
        self.about_label = about
        cv.addWidget(about)
        self.about_data = QLabel()
        self.about_data.setStyleSheet("color:#555")
        self.about_data.setWordWrap(True)
        cv.addWidget(self.about_data)
        self.btn_check = QPushButton("检查更新")
        self.btn_check.setMinimumWidth(120)
        self.btn_check.setMinimumHeight(34)
        self.btn_check.clicked.connect(lambda: self.check_update(manual=True))
        about_row.addWidget(self.btn_check, 0, Qt.AlignVCenter)
        # 设置项越来越多：放进可滚动区域，窗口矮的时候不会被压扁
        scroll = QScrollArea()
        scroll.setWidget(w)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # 表单的建议高度不能撑大整页，否则外层滚动区仍会把页底按钮推到屏幕外。
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(scroll, 1)
        tab_layout.addWidget(card)
        return tab

    # ---------- 查我的水平 ----------

    def _show_rank(self, res: dict):
        text = rank.describe(res)
        self.rank_result.setText(text or "<span style='color:#777'>填自己的「名字#编号」，查区内排名，自动选对应分段</span>")
        r = (res or {}).get("results") or []
        if hasattr(self, "my_rank_label"):
            self.my_rank_label.setText(f"你：{r[0]['band_name']}（前 {r[0]['top'] * 100:.1f}%）" if r else "")

    def _query_rank(self, silent: bool = False):
        if self._rank_worker and self._rank_worker.isRunning():
            return
        rid = self.rank_input.text().strip()
        if not silent:
            self.btn_rank.setEnabled(False)
            self.btn_rank.setText("查询中…")
        self._rank_worker = RankWorker(rid, self)
        self._rank_worker.done.connect(lambda res: self._rank_done(rid, res, silent))
        self._rank_worker.failed.connect(lambda msg: self._rank_failed(msg, silent))
        self._rank_worker.finished.connect(lambda: (self.btn_rank.setEnabled(True), self.btn_rank.setText("查询")))
        self._rank_worker.start()

    def _rank_done(self, rid: str, res: dict, silent: bool):
        import time
        s = self.engine.settings
        old = (s.my_rank or {}).get("results") or []
        old_band = norm_band(old[0]["band"]) if old else ""
        s.riot_id, s.my_rank = rid, {**res, "queried_at": time.time()}
        pending = getattr(self, "_recheck_pending", None)
        if silent and pending:  # 查分库更新后的自动重查成功了：这个日期不再查
            s.extra["rank_rechecked"] = pending
            self._recheck_pending = None
        s.save()
        self._show_rank(s.my_rank)
        band = norm_band(res["results"][0]["band"])
        if silent and old and band != old_band and band in BANDS:
            # 查分库更新后自动重查，分段变了：告诉玩家；参考分段原来跟着查分选的，就跟着换
            self._toast(f"查分数据更新了：你现在是{BANDS[band]}（前 {res['results'][0]['top'] * 100:.1f}%），"
                        f"原来是{BANDS.get(old_band, old_band)}")
            if (s.data_band or "") == old_band:
                silent = False
        if not silent and band in BANDS:
            i = self.band_box.findData(band)
            if i >= 0:
                self.band_box.setCurrentIndex(i)  # 触发 engine.set_band，记住选择

    def _rank_failed(self, msg: str, silent: bool):
        if silent:
            logging.getLogger(__name__).info("自动查分失败：%s", msg)
            return
        self.rank_result.setText(f"<span style='color:#d0533f'>{escape(msg)}</span>")

    def refresh_rank_if_stale(self):
        """启动时：上次查分超过 7 天就悄悄再查一次（不改你选的分段）。"""
        import time
        s = self.engine.settings
        if s.riot_id and time.time() - float((s.my_rank or {}).get("queried_at") or 0) > 7 * 86400:
            self._query_rank(silent=True)

    def recheck_rank_if_newer(self, cfg: dict) -> bool:
        """云控 rank.as_of（查分库最新数据日期）比本机上次查到的新：悄悄重查一次。
        同一个 as_of 查成功一次就不再查（查分库还没换完时不反复查）；查失败、对局中、选英雄中，下次刷新云控再试。"""
        s = self.engine.settings
        latest = str(((cfg or {}).get("rank") or {}).get("as_of") or "")
        mine = str((s.my_rank or {}).get("as_of") or "")
        if not (s.riot_id and latest and latest > mine and s.extra.get("rank_rechecked") != latest):
            return False
        if self._busy():  # 分段变了会弹提示：别在对局、选英雄时弹
            return False
        self._recheck_pending = latest
        if self.rank_input.text().strip() != s.riot_id:
            self.rank_input.setText(s.riot_id)
        logging.getLogger(__name__).info("查分库更新到 %s（本机上次 %s），自动重查", latest, mine or "无")
        self._query_rank(silent=True)
        return True

    # ---------- 云控 ----------

    def _show_primary(self):
        p = cloud.primary_link()
        if p:
            self.btn_comm.setText(p["name"])
            self.btn_comm.setToolTip(p.get("text") or "")
        self.btn_comm.setVisible(bool(p))

    def _open_primary(self):
        p = cloud.primary_link()
        if p:
            webbrowser.open(p["url"])

    def _in_game(self) -> bool:
        return self.engine.snapshot().get("phase") == "ingame"

    def _busy(self) -> bool:
        """对局中或正在选英雄：这时不弹窗（会抢游戏或客户端的焦点）。"""
        snap = self.engine.snapshot()
        return snap.get("phase") == "ingame" or bool(snap.get("champ_select"))

    def _modal_open(self) -> bool:
        """有弹窗开着（提醒类，或设置已保存、检查更新这类别的对话框）：不再叠一个。"""
        return self._popup_open or QApplication.activeModalWidget() is not None

    def _toast(self, text: str) -> None:
        tray = getattr(self, "tray", None)
        if tray:
            tray.showMessage("海克斯大乱斗助手", text, app_icon(), 6000)

    def _notify_tick(self):
        """游戏外才出：攒下的公告/新版本提示、数据已更新的提示、关了自动更新时的「是否更新」。
        弹窗还要避开选英雄；已经有弹窗开着时不再叠一个（计时器在弹窗打开期间也会跑）。"""
        if self._in_game() or self._modal_open():
            return
        if self._deferred_cloud is not None and not self._busy():
            cfg, self._deferred_cloud = self._deferred_cloud, None
            self._apply_cloud(cfg)
        if self._deferred_toast:
            text, self._deferred_toast = self._deferred_toast, None
            self._toast(text)
        dd = self.engine.snapshot().get("data") or {}
        note = dd.get("data_note")
        if note and note.get("id") != self._shown_note:
            self._shown_note = note["id"]
            logging.getLogger(__name__).info("右下角提示：%s", note["text"])
            self._toast(note["text"])
        offer = dd.get("data_offer")
        if offer and offer.get("id") not in self._asked_offers and not self._busy() and not self._modal_open():
            self._asked_offers.add(offer["id"])  # 每份新数据只问一次；点了「稍后」下次启动再问
            self._popup_open = True
            try:
                ask = QMessageBox.question(self, "统计数据", f"检测到新数据（{offer['text']}），现在更新吗？")
            finally:
                self._popup_open = False
            if ask == QMessageBox.Yes:
                st = self.engine.store
                threading.Thread(target=lambda: st.sync_data(force=True, manual=True), daemon=True).start()

    @staticmethod
    def _check_interval_ms() -> int:
        """定时检查的间隔：云控 update.check_minutes，每台电脑随机错开 ±10%，免得同一时刻一起请求。"""
        return int(cloud.check_minutes() * 60_000 * random.uniform(0.9, 1.1))

    def _upload_logs(self, cfg: dict) -> None:
        try:
            logupload.maybe_upload(self.engine.settings, cfg)
        except Exception:
            logging.getLogger(__name__).exception("上传日志出错")

    def _apply_cloud_settings(self, cfg: dict) -> None:
        """云控设置（cloudsettings.py）：改了值的马上生效，设置页跟着变；强制的原因每条规则提示一次。"""
        s = self.engine.settings
        res = cloudsettings.apply(s, cfg, __version__)
        changed = set(res["changed"])
        if changed:
            s.save()
            if "data_band" in changed:
                self.engine.set_band(s.data_band or None, by_user=False)
            if "game_lang" in changed:
                self.engine.set_game_lang(s.game_lang)
            if "check_data_updates" in changed and hasattr(self.engine.store, "check_updates"):
                self.engine.store.check_updates = s.check_data_updates
            if changed & {"overlay", "overlay_corner"} and hasattr(self, "overlay"):
                self.overlay.set_visible(s.overlay)
                self.overlay.panel.corner = s.overlay_corner
                act = getattr(getattr(self, "tray", None), "act_overlay", None)
                if act is not None:  # 托盘「显示游戏浮窗」的勾跟着变
                    act.blockSignals(True)
                    act.setChecked(s.overlay)
                    act.blockSignals(False)
            self._sync_settings_form()
        seen = s.extra.get("seen_force") if isinstance(s.extra.get("seen_force"), list) else []
        for rid, reason in res["reasons"]:
            if rid not in seen:
                seen.append(rid)
                s.extra["seen_force"] = seen[-20:]
                s.save()
                self._toast(reason)

    def _sync_settings_form(self) -> None:
        """设置页的控件按当前设置重新显示（云控改了值以后），不触发各自的处理函数。"""
        s = self.engine.settings
        if not hasattr(self, "set_data"):
            return
        pairs = [(self.set_data, lambda w: w.setChecked(s.check_data_updates)),
                 (self.set_corner, lambda w: w.setCurrentIndex(0 if s.overlay_corner == "top-right" else 1)),
                 (self.set_badge, lambda w: w.setCurrentIndex(max(0, w.findData(s.badge_position)))),
                 (self.set_speed, lambda w: w.setCurrentIndex(max(0, w.findData(s.speed)))),
                 (self.set_lang, lambda w: w.setCurrentIndex(max(0, w.findData(s.game_lang)))),
                 (self.band_box, lambda w: w.setCurrentIndex(max(0, w.findData(s.data_band or ""))))]
        for w, fn in pairs:
            old = w.blockSignals(True)
            try:
                fn(w)
            finally:
                w.blockSignals(old)
        self.badge_preview.set_position(s.badge_position)

    def _apply_cloud(self, cfg: dict):
        if hasattr(self.engine, "cloud_changed"):
            self.engine.cloud_changed()
        try:
            self._apply_cloud_settings(cfg)
        except Exception:
            logging.getLogger(__name__).exception("云控设置出错")
        try:
            logupload.apply_verbose(self.engine.settings, cfg)
        except Exception:
            logging.getLogger(__name__).exception("远程详细日志出错")
        try:
            self.recheck_rank_if_newer(cfg)
            threading.Thread(target=self._upload_logs, args=(cfg,), daemon=True).start()
        except Exception:
            logging.getLogger(__name__).exception("自动重查、上传日志出错")
        if hasattr(self, "_update_timer"):
            self._update_timer.setInterval(self._check_interval_ms())
        if hasattr(self, "about_label"):
            self.about_label.setText(about_html())
        if hasattr(self, "btn_comm"):
            self._show_primary()
        if self._busy() or self._modal_open():  # 公告、版本太旧都是弹窗：打完这局、选完英雄再弹；已有弹窗时也等一等
            if self._deferred_cloud is None:
                logging.getLogger(__name__).info("对局中或选英雄中，云控公告/版本检查推迟")
            self._deferred_cloud = cfg
            return
        self._popup_open = True
        try:
            self._cloud_popups(cfg)
        finally:
            self._popup_open = False

    def _cloud_popups(self, cfg: dict):
        s = self.engine.settings
        notice = cfg.get("notice") or {}
        if notice.get("id") and notice.get("text") and notice["id"] != s.seen_notice:
            s.seen_notice = notice["id"]
            s.save()
            box = QMessageBox(QMessageBox.Information, "公告", notice["text"], parent=self)
            b_open = box.addButton("查看", QMessageBox.AcceptRole) if notice.get("url") else None
            box.addButton("知道了", QMessageBox.RejectRole)
            box.exec()
            if b_open is not None and box.clickedButton() is b_open:
                webbrowser.open(notice["url"])
        minv = cfg.get("min_version") or ""
        if minv and update.is_newer(minv, __version__) and not getattr(self, "_min_warned", False):
            self._min_warned = True
            box = QMessageBox(QMessageBox.Warning, "需要更新", f"当前版本 {__version__} 太旧，请更新到最新版本。", parent=self)
            b_up = box.addButton("立即更新", QMessageBox.AcceptRole)
            box.addButton("稍后", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is b_up:
                self._update_info = self._update_info or getattr(self, "_held_update", None)
                if self._update_info:
                    self._start_update()
                else:
                    self._offer_manual("", update.pan_links())

    def _save_settings(self):
        s = self.engine.settings
        before = {k: getattr(s, k) for k in cloudsettings.SPEC}
        s.check_data_updates = self.set_data.isChecked()
        s.send_usage = self.set_usage.isChecked()
        cloud.set_install_id(s.install_id if s.send_usage else None)
        try:
            logupload.apply_verbose(s, cloud.get())  # 关了「帮助改善软件」，远程详细日志马上停
        except Exception:
            logging.getLogger(__name__).exception("远程详细日志出错")
        if hasattr(self.engine.store, "check_updates"):
            self.engine.store.check_updates = s.check_data_updates  # 马上生效，不用重开
        s.speed = self.set_speed.currentData() or "standard"
        s.overlay_corner = "top-right" if self.set_corner.currentIndex() == 0 else "top-left"
        s.badge_position = self.set_badge.currentData() or "top"
        lang = self.set_lang.currentData() or "zh_CN"
        if lang != s.game_lang:
            self.engine.set_game_lang(lang)
        mine = [k for k, v in before.items() if getattr(s, k) != v]
        s.mark_user(*mine)  # 玩家自己改的：以后云控改默认值不碰
        held = [k for k in mine if k in s.forced]
        for k in held:  # 云控正在强制的项：记下玩家要的值，云控撤掉后用它
            s.forced[k] = getattr(s, k)
        if held:
            self._apply_cloud_settings(cloud.get())  # 改回强制值，并让语言、数据更新等跟着生效
            QMessageBox.information(self, "设置", "这几项现在按官方临时设置，撤掉后会改回你选的："
                                    + "、".join(SETTING_NAMES.get(k, k) for k in held))
        saved = s.save()
        if lang == "ko_KR" and langpack.installed() is None and not (
                self._langpack_state and "error" not in self._langpack_state):
            ask = QMessageBox.question(self, "韩服识别数据",
                                       f"认韩文海克斯要先下载韩服识别数据（约 {langpack.KOREAN['size'] / 2 ** 20:.0f}MB），现在下载吗？")
            if ask == QMessageBox.Yes:
                self._download_langpack()
        self.overlay.set_visible(s.overlay)
        self.overlay.panel.corner = s.overlay_corner
        try:
            autostart.set_enabled(self.set_auto.isChecked())
        except OSError as e:
            QMessageBox.warning(self, "开机自启", f"设置失败：{e}")
        if saved:
            QMessageBox.information(self, "设置", "已保存。")
        else:
            QMessageBox.warning(self, "设置", "设置文件写不进去，这次的改动关掉助手后会丢失。")

    def _update_lang_hint(self):
        """游戏语言下面那一行：只有选了韩文才显示（识别数据下没下好、下载进度）。"""
        korean = self.set_lang.currentData() == "ko_KR"
        st = self._langpack_state
        busy = bool(st and "error" not in st)
        ready = langpack.installed() is not None
        if not korean:
            text = "国服选简体中文；外服按游戏客户端的语言选" if self.set_lang.currentData() == "zh_CN" else ""
        elif busy:
            text = f"正在下载韩服识别数据 {st['done'] / 2 ** 20:.1f} / {st['total'] / 2 ** 20:.1f} MB"
        elif st and "error" in st:
            text = f"下载失败：{st['error']}"
        elif ready:
            text = "韩服识别数据已下载"
        else:
            text = "韩服要先下载韩文识别数据才能认出海克斯"
        self.lang_hint.setText(text)
        self.lang_hint.setVisible(bool(text))
        self.btn_langpack.setVisible(korean and not ready and not busy)
        if not busy:
            self._langpack_timer.stop()

    def _download_langpack(self):
        if self._langpack_state and "error" not in self._langpack_state:
            return
        state = {"done": 0, "total": langpack.KOREAN["size"]}
        self._langpack_state = state

        def run():
            try:
                langpack.download(progress=lambda done, total: state.update(done=done, total=total))
                self._langpack_state = None
                if self.engine.settings.game_lang == "ko_KR":
                    self.engine.set_game_lang("ko_KR")  # 已经选了韩文：马上换上韩文模型
            except Exception as e:
                self._langpack_state = {"error": str(e)[:80]}
        threading.Thread(target=run, name="langpack", daemon=True).start()
        self._langpack_timer.start(300)
        self._update_lang_hint()

    def _recalibrate(self):
        self.engine.reset_calibration()
        QMessageBox.information(self, "重新校准", "已清除卡片位置，下次出现海克斯三选一时会重新校准。")

    # ---------- 更新 ----------

    def check_update(self, manual: bool = False):
        """启动、再次打开助手时各查一次，平时按云控 update.check_minutes 查（默认 3 小时）；
        只提示，用户点了「立即更新」才下载安装。正在查或正在下载时跳过。
        manual=True 是设置页「检查更新」按钮：结果（最新 / 有新版本 / 失败）都弹窗告诉用户。"""
        if self._worker and self._worker.isRunning():
            if manual:
                QMessageBox.information(self, "检查更新", "正在检查或下载更新，请稍候。")
            return
        self._data_msg = ""
        self._worker = UpdateWorker("check", parent=self, strict=manual,
                                    data_sync=self._sync_data_now if manual else None)
        self._worker.found.connect(lambda info: self._update_found(info, manual))
        self._worker.cloud.connect(self._apply_cloud)
        if manual:
            self.btn_check.setEnabled(False)
            self.btn_check.setText("检查中…")
            self._worker.data_done.connect(lambda m: setattr(self, "_data_msg", m))
            self._worker.uptodate.connect(lambda: QMessageBox.information(
                self, "检查更新", f"软件已是最新版本（{__version__}）。" + self._data_line()))
            self._worker.failed.connect(self._check_failed)
            self._worker.finished.connect(lambda: (self.btn_check.setEnabled(True),
                                                   self.btn_check.setText("检查更新")))
        self._worker.start()

    def _sync_data_now(self) -> str:
        """「检查更新」按钮：马上查统计数据（不管自动更新开关）和官方资料，返回结果。"""
        st = self.engine.store
        changed = st.sync_data(force=True, manual=True)
        if changed:  # 结果在「检查更新」的对话框里说了，右下角不再提示一遍
            self._shown_note = (getattr(st, "data_note", None) or {}).get("id")
        ds = st.dataset
        tail = f"（{ds.patch} 版本，截至 {str(ds.snapshot_at)[5:10]}）" if ds else ""
        if changed:
            msg = f"统计数据已更新{tail}。"
        elif st.sync_error:
            msg = "统计数据这次没连上，稍后会自动再试。"
        else:
            msg = f"统计数据已是最新{tail}。"
        # 官方资料（英雄、海克斯、装备、召唤师技能）平时按云控间隔查（可能 30 天一次），按钮上也马上查一次。
        # 只在换上新一版时多说一句；没变化、连不上都不提，免得多一行看不懂的话
        if hasattr(st, "sync_official"):
            try:
                if st.sync_official(force=True):
                    rev = st.official.status().get("official_revision")
                    msg += f"\n官方资料已更新（第 {rev} 版）。"
            except Exception:
                logging.getLogger(__name__).exception("检查官方资料出错")
        return msg

    def _data_line(self) -> str:
        msg = getattr(self, "_data_msg", "")
        return f"\n{msg}" if msg else ""

    def _check_failed(self, msg: str):
        logging.getLogger(__name__).warning("检查更新失败：%s", msg)
        box = QMessageBox(QMessageBox.Warning, "检查更新", "检查更新失败，请稍后再试。" + self._data_line(), parent=self)
        b_site = box.addButton("官网下载", QMessageBox.ActionRole)
        box.addButton("关闭", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is b_site:
            webbrowser.open(update.download_page())

    def _update_found(self, info: dict, manual: bool = False):
        known = (self._update_info or {}).get("version")
        minv = cloud.get().get("min_version") or ""
        if not manual and not (minv and update.is_newer(minv, __version__)) \
                and not cloud.offer_update(info["version"], getattr(self.engine.settings, "install_id", None)):
            # 分批推送还没轮到这台电脑（或暂停了）：先不提示；手动「检查更新」和「最低版本」照常
            self._held_update = info
            logging.getLogger(__name__).info("有新版本 %s，分批推送还没轮到本机，先不提示", info["version"])
            return
        self._update_info = info
        notes = f"<br><span style='color:#777'>{escape(info['notes'][:200])}</span>" if info.get("notes") else ""
        # 打包后的程序都能先试增量更新；源码运行时只有带安装包地址的才能直接装
        direct = patch.install_root() is not None or (bool(info.get("url")) and not is_portable())
        self.update_text.setText(f"<b>有新版本 {info['version']}</b>（当前 {__version__}）{notes}")
        self.btn_update.setText("立即更新" if direct else "去下载")
        self.update_bar.show()
        tray = getattr(self, "tray", None)
        if manual:
            ask = QMessageBox.question(self, "检查更新", f"有新版本 {info['version']}（当前 {__version__}）。"
                                       + (f"\n\n{info['notes']}" if info.get("notes") else "")
                                       + (f"\n\n{self._data_msg}" if getattr(self, "_data_msg", "") else "")
                                       + "\n\n现在更新吗？")
            if ask == QMessageBox.Yes:
                if self._worker:
                    self._worker.wait(3000)  # 检查线程发完信号就结束，等它收尾再开始下载
                self._start_update()
        elif tray and known != info["version"]:  # 同一个版本只弹一次通知；对局中先攒着
            text = f"有新版本 {info['version']}，打开主界面点「立即更新」。"
            if self._in_game():
                logging.getLogger(__name__).info("对局中，新版本提示推迟到打完")
                self._deferred_toast = text
            else:
                self._toast(text)

    def _start_update(self):
        if not self._update_info or (self._worker and self._worker.isRunning()):
            return
        # 源码运行、又没有安装包地址：只能打开下载页
        if patch.install_root() is None and (is_portable() or not self._update_info.get("url")):
            webbrowser.open(cloud.site())
            return
        if self.engine.snapshot().get("phase") == "ingame":
            QMessageBox.information(self, "更新", "正在对局中，打完这局再更新。")
            return
        self.btn_update.setEnabled(False)
        self.btn_update.setText("更新中…")
        self._update_stage = "准备更新…"
        show_progress(self.update_progress, self._update_stage)
        self._worker = UpdateWorker("download", info=self._update_info, parent=self)
        self._worker.phase.connect(self._update_phase)
        self._worker.progress.connect(
            lambda done, total: show_progress(self.update_progress, self._update_stage.rstrip("…"), done, total))
        self._worker.ready.connect(self._install)
        self._worker.patched.connect(self._apply_patch)
        self._worker.manual.connect(self._offer_manual)
        self._worker.failed.connect(self._update_failed)
        self._worker.start()

    def _update_phase(self, text: str):
        self._update_stage = text
        show_progress(self.update_progress, text)

    def _apply_patch(self, changed: list, stale: list):
        show_progress(self.update_progress, "下载完成，正在重启助手…", 1, 1)
        root = patch.install_root()
        try:
            patch.launch_apply(root, changed, stale, self._update_info["version"], relaunch=str(root / "AramHex.exe"))
        except OSError as e:
            return self._update_failed(str(e))
        self.tray.quit()  # 退出让出文件；替换完自动重新打开
        hard_exit_later()

    def _offer_manual(self, reason: str, links: dict | None = None):
        """更新没能在软件里完成：提示原因，按钮直接打开网盘下载（链接从官网读，每次发版会换）或官网下载区。"""
        self.update_progress.hide()
        self.btn_update.setEnabled(True)
        self.btn_update.setText("重试更新")
        links = links or {}
        pan = links.get("portable" if is_portable() else "setup")
        box = QMessageBox(QMessageBox.Information, "更新", f"{reason}请下载新版本，直接覆盖安装，设置会保留。", parent=self)
        b_pan = box.addButton("网盘下载", QMessageBox.AcceptRole)
        b_site = box.addButton("官网下载", QMessageBox.ActionRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(b_pan)
        box.exec()
        if box.clickedButton() is b_pan:
            webbrowser.open(pan or update.download_page())
        elif box.clickedButton() is b_site:
            webbrowser.open(update.download_page())

    def _install(self, path: str):
        show_progress(self.update_progress, "下载完成，正在启动安装程序…", 1, 1)
        try:
            update.launch_installer(path)
        except OSError as e:
            return self._update_failed(str(e))
        self.tray.quit()  # 退出让安装程序替换文件；装完会自动重新打开
        hard_exit_later()

    def _update_failed(self, msg: str):
        logging.getLogger(__name__).warning("更新失败：%s", msg)
        self._offer_manual("更新失败。")

    def _ensure_completer(self):
        heroes = self.engine.store.heroes
        if self._names or not heroes:
            return
        comp, self._names = hero_completer(self.engine.store, self)
        comp.activated.connect(lambda text: (self.engine.set_hero(self._names.get(text, text)), self.search.clear()))
        self.search.setCompleter(comp)

    def _search_pick(self):
        t = self.search.text().strip()
        if t:
            self.engine.set_hero(self._names.get(t, t))
            self.search.clear()

    def _set_candidates(self, cands):
        ids = [c.get("id") for c in cands]
        if ids == self._cand_ids:
            return
        self._cand_ids = ids
        while self.cands.count():
            it = self.cands.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if cands:
            self.cands.addWidget(QLabel("候选："))
        for c in cands[:6]:
            b = QPushButton(c.get("name") or c.get("id"))
            b.clicked.connect(lambda _=False, hid=c.get("id"): self.engine.set_hero(hid))
            self.cands.addWidget(b)
        self.cands.addStretch(1)

    # ---------- 刷新 ----------

    def refresh(self):
        if not self.isVisible():
            return
        snap = self.engine.snapshot()
        self._ensure_completer()
        dd = snap.get("data", {})
        v = snap.get("vision", {})
        self.status.setText(
            f"<b>状态：</b>{PHASE_CN.get(snap.get('phase'), snap.get('phase'))}<br>"
            f"<b>海克斯识别：</b>{VISION_CN.get(v.get('status'), v.get('status'))}"
            f"{' · <span style=\"color:#d0533f\">游戏可能是全屏模式，无法识别。请在游戏内设置-窗口模式中选择无边框/窗口。</span>' if v.get('blank') else ''}<br>"
            f"<b>数据：</b>{BANDS.get(dd.get('band_effective'), '全体')} · {data_status_html(dd)}")
        if hasattr(self, "about_data"):
            self.about_data.setText("统计数据：" + data_status_html(dd))
        dp = dd.get("data_progress")
        if dp:
            show_progress(self.data_progress, "统计数据更新中", dp.get("done"), dp.get("total"))
        else:
            self.data_progress.hide()

        h, hid = snap.get("hero"), snap.get("hero_id")
        show_win = snap.get("settings", {}).get("show_winrate", True)
        d = self.engine.hero_panel(hid) if hid else None
        if h:
            r = (d or {}).get("rank") or {}
            b = self.engine.hero_brief(hid)
            rating = []
            if b.get("grade"):
                rating.append(f"评级 {b['grade']}")
            if b.get("rank"):
                rating.append(f"胜率排名 {b['rank']}/{b.get('total') or '?'}")
            if show_win and r.get("win") is not None:
                rating.append(f"胜率 {_pct(r['win'])}")
            if r.get("pick") is not None:
                rating.append(f"出场率 {_pct(r['pick'])}")
            self.hero_label.setText(
                f"<span style='font-size:18px'><b>{h['name']}</b></span> {h['nick']}<br>"
                f"{' · '.join(rating)}")
        else:
            self.hero_label.setText("<b>未识别到英雄，请手动选择</b>")
        cs = snap.get("champ_select")
        if cs and (cs.get("bench") or cs.get("cards")):
            from .overlay import champ_select_summary
            self.cs_label.setText(champ_select_summary(cs, show_win))
            self.cs_label.show()
        else:
            self.cs_label.hide()
        self._set_candidates(snap.get("candidates") or [])

        key = (hid, d.get("date"), d.get("data_revision"), self.engine.icons.version, dd.get("band_effective")) if d else None
        if d and self._panel_hero != key:
            self._panel_hero = key
            self.tab_hero.setText(self._hero_html(d, show_win))
        elif not d:
            self._panel_hero = None
            self.tab_hero.setText("<span style='color:#777'>还没有选英雄</span>")
        self._render_offer(snap.get("evaluation"), show_win)

    def _hero_html(self, d, show_win) -> str:
        out = buildview.lines(d, self.engine.icons, show_win, size=28)
        sk = buildview.skills_line(d, show_win)
        if sk:
            out.append(sk)
        top = [a for a in d.get("augments", []) if a.get("delta") is not None and a.get("sample") != "low"]
        top.sort(key=lambda a: -a["delta"])
        if top:
            out.append("<b>推荐海克斯</b>")
            for a in top[:8]:
                out.append(f"　{a['name']} <span style='color:#777'>{RARITY_CN.get(a.get('rarity'), '')}"
                           f" {a.get('tier') or ''}</span> <b style='color:#2e9d5b'>{a['delta']:+.1f}</b>")
        return "<br>".join(out)

    def _render_offer(self, ev, show_win):
        if not ev:
            self.offer_advice.setText("<span style='color:#777'>未在选海克斯</span>")
            for lab in self.offer_cards:
                lab.setText("")
            return
        pk = recommend.pentakill_text(ev.get("pentakill"))
        self.offer_advice.setText(f"<b style='font-size:15px'>{ev['advice']}</b>"
                                  + (f"<br><span style='color:#c9a0ff'>{escape(pk)}</span>" if pk else ""))
        for lab, r in zip(self.offer_cards, ev["slots"]):
            v, col = VERDICT_CN.get(r["verdict"], VERDICT_CN["unknown"])
            if r["verdict"] == "best" and r.get("sample_warning"):
                v = "相对较好，仅供参考"
            lines = [f"<b style='color:{col}'>{v}</b>", f"<b>{r.get('name') or '未识别'}</b>"]
            meta = [RARITY_CN.get(r.get("rarity"), "")]
            if r.get("tier"):
                meta.append(f"{r['tier']}级")
            lines.append("<span style='color:#777'>" + " ".join(m for m in meta if m) + "</span>")
            if r["verdict"] == "nodata":
                note = recommend.NEW_NOTE if r.get("basis") == "new" else recommend.NODATA_NOTE
                lines.append(f"<span style='color:#777'>{note}</span>")
            if r.get("basis") == "pbe":
                pbe = f"测试服 {r['pbe_games']} 局 · 胜率 {_pct(r['pbe_win'])}，" if r.get("pbe_games") else ""
                lines.append(f"<span style='color:#777'>{pbe}{recommend.PBE_NOTE}</span>")
            if r.get("score") is not None:
                s = f"{r['score']:+.1f}"
                if show_win and recommend.win_text(r):
                    s += " · " + recommend.win_text(r)
                if r.get("thin") or r.get("sample_warning"):
                    warn = "⚠ 样本太少" if r.get("thin") == "too_few" else "样本偏少"
                    s += f" · <span style='color:#ff7a6b'>{warn}</span>"
                lines.append(s)
            lab.setText("<br>".join(lines))

    # ---------- 窗口行为 ----------

    def show_and_raise(self):
        screen = self.screen()
        if screen:
            available = screen.availableGeometry()
            self.resize(min(self.width(), max(360, available.width() - 16)),
                        min(self.height(), max(260, available.height() - 48)))
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()
        self.refresh()

    def closeEvent(self, e):
        if QApplication.instance().property("quitting"):
            e.accept()
            return
        e.ignore()
        self.hide()
        if not self._hint_shown and QSystemTrayIcon.isSystemTrayAvailable():
            self._hint_shown = True
            tray = getattr(self, "tray", None)
            if tray:
                tray.showMessage("海克斯大乱斗助手", "助手仍在后台运行，右下角托盘图标可以重新打开或退出。",
                                 app_icon(), 4000)


class Tray(QSystemTrayIcon):
    def __init__(self, engine, window: MainWindow, overlay):
        super().__init__(app_icon())
        self.engine, self.window, self.overlay = engine, window, overlay
        self.setToolTip(f"海克斯大乱斗助手 v{__version__}")
        m = QMenu()
        m.addAction("打开主界面", window.show_and_raise)
        self.act_overlay = QAction("显示游戏浮窗", m, checkable=True)
        self.act_overlay.setChecked(engine.settings.overlay)
        self.act_overlay.toggled.connect(self._toggle_overlay)
        m.addAction(self.act_overlay)
        m.addAction("重新识别", engine.rescan)
        m.addAction("检查更新", lambda: (window.show_and_raise(), window.check_update(manual=True)))
        self.messageClicked.connect(window.show_and_raise)
        self.act_auto = QAction("开机自动启动", m, checkable=True)
        self.act_auto.setChecked(autostart.is_enabled())
        self.act_auto.toggled.connect(self._toggle_auto)
        m.addAction(self.act_auto)
        m.addSeparator()
        m.addAction("退出", self.quit)
        self.setContextMenu(m)
        self._menu = m
        self.activated.connect(self._activated)
        self.timer = QTimer()
        self.timer.timeout.connect(self._tooltip)
        self.timer.start(3000)

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.window.show_and_raise()

    def _toggle_overlay(self, on: bool):
        s = self.engine.settings
        s.set_by_user("overlay", on)  # 云控正在强制时只记下玩家要的值
        s.save()
        self.overlay.set_visible(s.overlay)
        if s.overlay != on:
            self.act_overlay.blockSignals(True)
            self.act_overlay.setChecked(s.overlay)
            self.act_overlay.blockSignals(False)
            self.showMessage("海克斯大乱斗助手", "浮窗现在按官方临时设置，撤掉后改回你选的", app_icon(), 6000)

    def _toggle_auto(self, on: bool):
        try:
            autostart.set_enabled(on)
        except OSError:
            pass

    def _tooltip(self):
        s = self.engine.snapshot()
        h = s.get("hero")
        self.setToolTip(f"海克斯大乱斗助手 v{__version__}\n" + PHASE_CN.get(s.get("phase"), "") +
                        (f"\n英雄：{h['name']}" if h else ""))

    def quit(self):
        self.hide()
        quit_app(self.engine)
