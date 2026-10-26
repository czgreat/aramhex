"""设置表单可滚动，页底检查更新始终可见，公共英雄区域在三页保持一致。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtTest import QTest


@pytest.fixture
def mw(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    # 其他 GUI 测试遗留的首次欢迎定时器也可能在 processEvents 时触发。
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui.mainwindow import MainWindow
    from lolhex.ui.overlay import Overlay

    settings = Settings.load()
    settings.seen_welcome = True
    engine = Engine(settings)
    overlay = Overlay(engine)
    window = MainWindow(engine, overlay)
    overlay.timer.stop()
    window.show()
    window.refresh()
    for timer in window.findChildren(QTimer):
        timer.stop()
    yield window
    window.hide()
    overlay.set_visible(False)
    window.deleteLater()
    overlay.panel.deleteLater()
    overlay.badges.deleteLater()
    overlay.champ.deleteLater()
    engine.stop()
    app.processEvents()


def settle():
    for _ in range(5):
        QtWidgets.QApplication.processEvents()


def fully_visible(widget):
    """isVisible 本身不能发现按钮已被某层滚动区裁掉。"""
    assert widget.isVisible()
    rect = widget.rect()
    node = widget
    while node.parentWidget():
        rect.translate(node.pos())
        node = node.parentWidget()
        assert node.rect().contains(rect)


@pytest.mark.parametrize("size", [(700, 820), (700, 720), (700, 680), (700, 526), (667, 424)])
def test_update_visible_without_scrolling(mw, monkeypatch, size):
    mw.resize(*size)
    mw.tabs.setCurrentIndex(2)
    settle()
    outer = mw.centralWidget()
    assert outer.verticalScrollBar().value() == 0
    fully_visible(mw.btn_check)
    calls = []
    monkeypatch.setattr(mw, "check_update", lambda manual=False: calls.append(manual))
    QTest.mouseClick(mw.btn_check, Qt.LeftButton)
    assert calls == [True]


def test_scrolling_form_does_not_move_footer(mw):
    mw.resize(667, 424)
    mw.tabs.setCurrentIndex(2)
    settle()
    position = mw.btn_check.mapTo(mw, QPoint())
    form = mw.tabs.widget(2).findChild(QtWidgets.QScrollArea)
    bar = form.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum())
    settle()
    bar.setValue(mw.rank_input.mapTo(form.widget(), QPoint()).y())
    settle()
    fully_visible(mw.rank_input)
    fully_visible(mw.btn_check)
    assert mw.btn_check.mapTo(mw, QPoint()) == position
    bar.setValue(0)
    settle()
    fully_visible(mw.set_lang)


@pytest.mark.parametrize("page", [0, 1, 2, 0, 2, 1])
def test_common_hero_controls_stay_visible(mw, page):
    mw.resize(700, 720)
    mw.tabs.setCurrentIndex(page)
    settle()
    fully_visible(mw.search)
    fully_visible(mw.band_box)


@pytest.mark.parametrize("state,ready,download_visible", [
    (None, False, True),
    ({"done": 1024, "total": 2048}, False, False),
    ({"error": "连接超时"}, False, True),
    (None, True, False),
])
def test_korean_states_preserved(mw, monkeypatch, state, ready, download_visible):
    from lolhex.ui import mainwindow
    monkeypatch.setattr(mainwindow.langpack, "installed", lambda: "model" if ready else None)
    mw.tabs.setCurrentIndex(2)
    mw._langpack_state = state
    mw.set_lang.setCurrentIndex(mw.set_lang.findData("ko_KR"))
    mw._update_lang_hint()
    settle()
    assert mw.lang_hint.text()
    assert mw.btn_langpack.isVisibleTo(mw) == download_visible
    fully_visible(mw.btn_check)


def test_other_pages_keep_long_content_scrollable(mw):
    """出装页很长时在自己的滚动区里滚，不撑高整个窗口，切到设置页「检查更新」照样看得到。"""
    mw.resize(700, 526)
    mw.tabs.setCurrentIndex(0)
    mw.tab_hero.setText("<br>".join(["出装与加点"] * 100))
    settle()
    page = mw.tabs.widget(0)
    assert page.verticalScrollBar().maximum() > 0
    assert mw.centralWidget().verticalScrollBar().maximum() == 0
    mw.tabs.setCurrentIndex(2)
    settle()
    fully_visible(mw.btn_check)


@pytest.mark.parametrize("size", [(700, 720), (700, 526), (667, 424)])
def test_update_visible_with_real_hero_selected(mw, size):
    """选了英雄（格温，用内置的真实出装资料）后，设置页的「检查更新」不能被出装页撑到窗口外（Codex 10-26 复现）。"""
    mw.resize(*size)
    mw.engine.set_hero("887")
    mw.refresh()
    assert mw.tab_hero.text()  # 出装页确实有内容
    mw.tabs.setCurrentIndex(2)
    settle()
    assert mw.centralWidget().verticalScrollBar().value() == 0
    fully_visible(mw.btn_check)


def test_long_data_status_does_not_push_update_off_the_right_edge(mw):
    from lolhex.ui.data_status import data_status_html
    mw.resize(667, 424)
    mw.tabs.setCurrentIndex(2)
    mw.about_data.setText("统计数据：" + data_status_html({
        "dataset": True, "dataset_patch": "16.21", "patch": "16.22",
        "dataset_matches": 1090000, "dataset_snapshot": "2026-10-22T12:00:00+08:00",
        "event_fallback": True, "event_label": "活动模式", "error": "离线",
    }))
    settle()
    assert mw.centralWidget().horizontalScrollBar().maximum() == 0
    fully_visible(mw.btn_check)
