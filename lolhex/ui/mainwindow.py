"""主界面与系统托盘。

- 主窗口：数据状态、当前英雄（识别结果/候选/搜索手动选）、英雄评级与出装加点、当前三选一推荐、设置。
- 关闭主窗口只是隐藏到托盘；退出要在托盘菜单里点「退出」。
- 托盘：双击打开主界面；右键菜单。
"""

from __future__ import annotations

import logging
import os
import threading
import webbrowser
from html import escape
from importlib import resources

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar, QScrollArea,
                               QPushButton, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget)

from .. import DATA_HOMEPAGE, HELP_PAGE, HOMEPAGE, WEBSITE, __version__, cloud, patch, rank, recommend, update
from ..config import SPEEDS, is_portable
from ..vision import window as win
from ..data.public import BANDS
from . import autostart
from .autoshot import shots_dir
from . import buildview
from .data_status import data_status_html
from .herosearch import hero_completer

RARITY_CN = {"silver": "白银", "gold": "黄金", "prismatic": "棱彩"}
VERDICT_CN = {"best": ("首选", "#2e9d5b"), "ok": ("可选", "#3b7dd8"), "reroll": ("可考虑刷新", "#d0533f"),
              "unknown": ("未知", "#888"), "nodata": ("无数据", "#888")}
PHASE_CN = {None: "未检测到英雄联盟", "client": "客户端（选英雄界面会自动识别英雄）", "ingame": "对局中"}
VISION_CN = {"idle": "待机", "paused": "游戏不在前台，暂停", "scanning": "扫描中"}
UPDATE_EVERY_MS = 30 * 60 * 1000  # 常驻时多久查一次更新


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
    links = [f"<a href='{WEBSITE}'>官网</a>", f"<a href='{HOMEPAGE}'>GitHub</a>", f"<a href='{DATA_HOMEPAGE}'>统计数据</a>"]
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
        self.setMinimumSize(640, 700)

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
        self.btn_dash.clicked.connect(lambda: webbrowser.open(HELP_PAGE))
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
        self._update_timer.start(UPDATE_EVERY_MS)
        # 提示和弹窗都不在对局中出现（会抢游戏的焦点）：对局中先攒着，回到游戏外再出
        self._deferred_cloud: dict | None = None
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
        self.tabs.addTab(w1, "出装与加点")

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
        self.setCentralWidget(root)

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
        self.set_win = QCheckBox("显示胜率数字")
        self.set_win.setChecked(s.show_winrate)
        self.set_live = QCheckBox("对局中用游戏接口（认英雄更准）")
        self.set_live.setChecked(s.use_liveclient)
        self.set_corner = QComboBox()
        self.set_corner.addItems(["右上角", "左上角"])
        self.set_corner.setCurrentIndex(0 if s.overlay_corner == "top-right" else 1)
        self.set_overlay = QCheckBox("显示游戏内浮窗")
        self.set_overlay.setChecked(s.overlay)
        self.set_speed = QComboBox()
        for key, (label, _) in SPEEDS.items():
            self.set_speed.addItem(label, key)
        self.set_speed.setCurrentIndex(max(0, self.set_speed.findData(s.speed)))
        self.set_capture = QCheckBox("录屏、截图时显示浮窗")
        self.set_capture.setChecked(s.allow_capture)
        self.set_autoshot = QCheckBox("选英雄、三选一时自动截图")
        self.set_autoshot.setChecked(s.auto_screenshot)
        btn_shots = QPushButton("打开截图文件夹")
        btn_shots.clicked.connect(lambda: os.startfile(str(shots_dir())) if hasattr(os, "startfile") else None)
        shot_row = QHBoxLayout()
        shot_row.addWidget(self.set_autoshot, 1)
        shot_row.addWidget(btn_shots)
        self.set_auto = QCheckBox("开机自动启动")
        self.set_auto.setChecked(autostart.is_enabled())
        self.set_data = QCheckBox("自动更新统计数据")
        self.set_data.setChecked(s.check_data_updates)
        f.addRow("识别频率", self.set_speed)
        f.addRow("", self.set_win)
        f.addRow("", self.set_overlay)
        f.addRow("浮窗位置", self.set_corner)
        f.addRow("", self.set_capture)
        f.addRow("", shot_row)
        f.addRow("", self.set_live)
        f.addRow("", self.set_auto)
        f.addRow("", self.set_data)
        save = QPushButton("保存设置")
        save.clicked.connect(self._save_settings)
        f.addRow("", save)

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
        card = _card()
        cv = QVBoxLayout(card)
        cv.setContentsMargins(18, 16, 18, 16)
        cv.setSpacing(8)
        about = QLabel(about_html())
        about.setTextFormat(Qt.RichText)
        about.setWordWrap(True)
        about.setOpenExternalLinks(True)
        self.about_label = about
        cv.addWidget(about)
        self.about_data = QLabel()
        self.about_data.setStyleSheet("color:#555")
        cv.addWidget(self.about_data)
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        self.btn_check = QPushButton("检查更新")
        self.btn_check.setMinimumWidth(140)
        self.btn_check.setMinimumHeight(34)
        self.btn_check.clicked.connect(lambda: self.check_update(manual=True))
        row.addWidget(self.btn_check)
        row.addStretch(1)
        cv.addLayout(row)
        outer.addWidget(card)
        # 设置项越来越多：放进可滚动区域，窗口矮的时候不会被压扁
        scroll = QScrollArea()
        scroll.setWidget(w)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        return scroll

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
        s.riot_id, s.my_rank = rid, {**res, "queried_at": time.time()}
        s.save()
        self._show_rank(s.my_rank)
        band = res["results"][0]["band"]
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

    def _toast(self, text: str) -> None:
        tray = getattr(self, "tray", None)
        if tray:
            tray.showMessage("海克斯大乱斗助手", text, app_icon(), 6000)

    def _notify_tick(self):
        """游戏外才出：攒下的公告/新版本提示、数据已更新的提示、关了自动更新时的「是否更新」。"""
        if self._in_game():
            return
        if self._deferred_cloud is not None:
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
        if offer and offer.get("id") not in self._asked_offers:
            self._asked_offers.add(offer["id"])  # 每份新数据只问一次；点了「稍后」下次启动再问
            ask = QMessageBox.question(self, "统计数据", f"检测到新数据（{offer['text']}），现在更新吗？")
            if ask == QMessageBox.Yes:
                st = self.engine.store
                threading.Thread(target=lambda: st.sync_data(force=True, manual=True), daemon=True).start()

    def _apply_cloud(self, cfg: dict):
        if hasattr(self, "about_label"):
            self.about_label.setText(about_html())
        if hasattr(self, "btn_comm"):
            self._show_primary()
        if self._in_game():  # 公告、版本太旧都是弹窗：打完这局再弹
            if self._deferred_cloud is None:
                logging.getLogger(__name__).info("对局中，云控公告/版本检查推迟到打完")
            self._deferred_cloud = cfg
            return
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
                if self._update_info:
                    self._start_update()
                else:
                    self._offer_manual("", update.pan_links())

    def _save_settings(self):
        s = self.engine.settings
        s.check_data_updates = self.set_data.isChecked()
        if hasattr(self.engine.store, "check_updates"):
            self.engine.store.check_updates = s.check_data_updates  # 马上生效，不用重开
        s.speed =self.set_speed.currentData() or "standard"
        s.allow_capture = self.set_capture.isChecked()
        s.auto_screenshot = self.set_autoshot.isChecked()
        win.set_capture_allowed(s.allow_capture)
        s.show_winrate = self.set_win.isChecked()
        s.use_liveclient = self.set_live.isChecked()
        s.overlay = self.set_overlay.isChecked()
        s.overlay_corner = "top-right" if self.set_corner.currentIndex() == 0 else "top-left"
        s.save()
        self.overlay.set_visible(s.overlay)
        self.overlay.panel.corner = s.overlay_corner
        try:
            autostart.set_enabled(self.set_auto.isChecked())
        except OSError as e:
            QMessageBox.warning(self, "开机自启", f"设置失败：{e}")
        QMessageBox.information(self, "设置", "已保存。")

    def _recalibrate(self):
        self.engine.reset_calibration()
        QMessageBox.information(self, "重新校准", "已清除卡片位置，下次出现海克斯三选一时会重新校准。")

    # ---------- 更新 ----------

    def check_update(self, manual: bool = False):
        """启动、再次打开助手时各查一次，平时每 UPDATE_EVERY_MS 查一次；
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
        """「检查更新」按钮：马上查统计数据（不管自动更新开关），返回一句结果。"""
        st = self.engine.store
        changed = st.sync_data(force=True, manual=True)
        if changed:  # 结果在「检查更新」的对话框里说了，右下角不再提示一遍
            self._shown_note = (getattr(st, "data_note", None) or {}).get("id")
        ds = st.dataset
        tail = f"（{ds.patch} 版本，截至 {str(ds.snapshot_at)[5:10]}）" if ds else ""
        if changed:
            return f"统计数据已更新{tail}。"
        if st.sync_error:
            return "统计数据这次没连上，稍后会自动再试。"
        return f"统计数据已是最新{tail}。"

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
            webbrowser.open(update.DOWNLOAD_PAGE)

    def _update_found(self, info: dict, manual: bool = False):
        known = (self._update_info or {}).get("version")
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
            webbrowser.open(WEBSITE)
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
            webbrowser.open(pan or update.DOWNLOAD_PAGE)
        elif box.clickedButton() is b_site:
            webbrowser.open(update.DOWNLOAD_PAGE)

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
            f"{' · <span style=\"color:#d0533f\">截图全黑，请把游戏改成无边框</span>' if v.get('blank') else ''}<br>"
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
            self.hero_label.setText("<b>未识别到英雄</b>")
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
        self.offer_advice.setText(f"<b style='font-size:15px'>{ev['advice']}</b>")
        for lab, r in zip(self.offer_cards, ev["slots"]):
            v, col = VERDICT_CN.get(r["verdict"], VERDICT_CN["unknown"])
            lines = [f"<b style='color:{col}'>{v}</b>", f"<b>{r.get('name') or '未识别'}</b>"]
            meta = [RARITY_CN.get(r.get("rarity"), "")]
            if r.get("tier"):
                meta.append(f"{r['tier']}级")
            lines.append("<span style='color:#777'>" + " ".join(m for m in meta if m) + "</span>")
            if r["verdict"] == "nodata":
                note = recommend.NEW_NOTE if r.get("basis") == "new" else recommend.NODATA_NOTE
                lines.append(f"<span style='color:#777'>{note}</span>")
            if r.get("basis") == "pbe":
                lines.append(f"<span style='color:#777'>{recommend.PBE_NOTE}</span>")
            if r.get("score") is not None:
                s = f"{r['score']:+.1f}"
                if show_win and r.get("win") is not None:
                    s += f" · 胜率 {_pct(r['win'])}"
                if r.get("thin"):
                    warn = "⚠ 样本太少" if r["thin"] == "too_few" else "样本偏少"
                    s += f" · <span style='color:#ff7a6b'>{warn}</span>"
                lines.append(s)
            lab.setText("<br>".join(lines))

    # ---------- 窗口行为 ----------

    def show_and_raise(self):
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
        if engine.debug and hasattr(os, "startfile"):
            m.addAction("打开调试文件夹", lambda: (engine.debug.root.mkdir(parents=True, exist_ok=True),
                                             os.startfile(str(engine.debug.root))))
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
        self.engine.settings.overlay = on
        self.engine.settings.save()
        self.overlay.set_visible(on)

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
