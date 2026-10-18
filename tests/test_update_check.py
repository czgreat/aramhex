"""助手常驻托盘时也要能查到更新：再次打开、定时都会查，正在查或下载时不重复；
设置页「检查更新」按钮把结果都弹窗告诉用户。"""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from lolhex.ui import mainwindow  # noqa: E402
from lolhex.ui.mainwindow import MainWindow  # noqa: E402


class FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)

    def emit(self, *a):
        for fn in self.slots:
            fn(*a)


class FakeWorker:
    started = []

    def __init__(self, mode, info=None, parent=None, strict=False, data_sync=None):
        self.mode, self.strict, self.running, self.data_sync = mode, strict, False, data_sync
        self.found, self.uptodate, self.failed, self.finished, self.data_done, self.cloud = (FakeSignal() for _ in range(6))

    def start(self):
        FakeWorker.started.append(self)
        self.running = True

    def isRunning(self):
        return self.running

    def wait(self, ms):
        return True


class FakeButton:
    def __init__(self):
        self.enabled, self.text = True, ""

    def setEnabled(self, v):
        self.enabled = v

    def setText(self, t):
        self.text = t


def _win():
    shown = []
    win = SimpleNamespace(
        engine=SimpleNamespace(settings=SimpleNamespace()),
        _worker=None, _update_info=None, btn_check=FakeButton(), btn_update=FakeButton(),
        update_text=SimpleNamespace(setText=lambda t: None),
        update_bar=SimpleNamespace(show=lambda: None),
        tray=SimpleNamespace(showMessage=lambda *a: shown.append(a[1])),
    )
    win.check_update = lambda manual=False: MainWindow.check_update(win, manual)
    win._sync_data_now = lambda: "统计数据已是最新。"
    win._data_line = lambda: MainWindow._data_line(win)
    win._apply_cloud = lambda cfg: None
    win._in_game = lambda: False
    win._toast = lambda t: MainWindow._toast(win, t)
    win._update_found = lambda info, manual=False: MainWindow._update_found(win, info, manual)
    win._check_failed = lambda msg: shown.append("失败：" + msg)
    win._start_update = lambda: shown.append("开始下载")
    return win, shown


@pytest.fixture
def boxes(monkeypatch):
    said = []
    monkeypatch.setattr(mainwindow, "UpdateWorker", FakeWorker)
    FakeWorker.started = []
    monkeypatch.setattr(mainwindow.QMessageBox, "information", lambda parent, title, text: said.append(text))
    return said


def test_check_skips_while_previous_still_running(boxes):
    win, _ = _win()
    win.check_update()
    win.check_update()  # 上一次还没查完
    assert len(FakeWorker.started) == 1 and not boxes  # 自动检查不打扰用户
    win._worker.running = False
    win.check_update()  # 再次打开助手 / 定时器：再查一次
    assert len(FakeWorker.started) == 2


def test_tray_notice_once_per_version():
    win, shown = _win()
    info = {"version": "0.3.31", "file": "AramHex-Setup-0.3.31.exe", "sha256": "0" * 64, "url": "http://x/a.exe"}
    win._update_found(info)
    win._update_found(dict(info))  # 半小时后又查到同一个版本：只更新横条，不再弹通知
    win._update_found({**info, "version": "0.3.32"})
    assert len(shown) == 2 and "0.3.32" in shown[1]


def test_manual_check_reports_every_outcome(boxes, monkeypatch):
    win, shown = _win()
    win.check_update(manual=True)
    w = FakeWorker.started[-1]
    assert w.strict and not win.btn_check.enabled and w.data_sync  # 手动检查顺带同步统计数据
    w.data_done.emit(w.data_sync())
    w.uptodate.emit()
    w.finished.emit()
    assert "最新版本" in boxes[-1] and "统计数据已是最新" in boxes[-1] and win.btn_check.enabled

    win._worker.running = False
    win.check_update(manual=True)
    FakeWorker.started[-1].failed.emit("连接超时")
    assert shown[-1] == "失败：连接超时"

    win._worker.running = False
    monkeypatch.setattr(mainwindow.QMessageBox, "question", lambda *a: mainwindow.QMessageBox.Yes)
    win.check_update(manual=True)
    FakeWorker.started[-1].found.emit({"version": "9.9.9", "file": "AramHex-Setup-9.9.9.exe", "sha256": "0" * 64})
    assert shown[-1] == "开始下载"  # 用户点"是"直接下载，不再弹托盘通知

def test_mirror_result_offers_download_page():
    """GitHub 连不上、从镜像查到的新版本：没有安装包地址，按钮变成「去下载」。"""
    win, _ = _win()
    win._update_found({"version": "9.9.9", "file": None, "sha256": None, "url": None, "page": "p", "mirror": True})
    assert win.btn_update.text == "去下载"
    win._update_found({"version": "9.9.10", "file": "AramHex-Setup-9.9.10.exe", "sha256": "0" * 64, "url": "u"})
    assert win.btn_update.text in ("立即更新", "去下载")  # 便携版跑测试时也是「去下载」


def test_popups_wait_until_game_ends(monkeypatch):
    # 对局中：公告不弹、新版本提示不出，攒到回到游戏外再出
    monkeypatch.setattr(mainwindow.QMessageBox, "exec", lambda self: pytest.fail("对局中不该弹窗"))
    shown = []
    win = SimpleNamespace(engine=SimpleNamespace(settings=SimpleNamespace(seen_notice="")), _deferred_cloud=None,
                          _deferred_toast=None, _update_info=None, btn_update=FakeButton(),
                          update_text=SimpleNamespace(setText=lambda t: None), update_bar=SimpleNamespace(show=lambda: None),
                          tray=SimpleNamespace(showMessage=lambda *a: shown.append(a[1])))
    win._in_game = lambda: True
    cfg = {"notice": {"id": "n1", "text": "公告"}}
    MainWindow._apply_cloud(win, cfg)
    assert win._deferred_cloud == cfg
    MainWindow._update_found(win, {"version": "9.9.9"})
    assert not shown and "9.9.9" in win._deferred_toast
