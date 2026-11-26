"""主界面样式：只落在主窗口、对话框、托盘菜单上，图标文件齐全，按钮是中文，浮窗一个像素不变。"""
import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
Q = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtGui import QColor, QImage, QPalette

from lolhex.ui import theme


@pytest.fixture
def app():
    app = Q.QApplication.instance() or Q.QApplication([])
    style, palette, sheet = app.style().objectName(), app.palette(), app.styleSheet()
    yield app
    app.setStyleSheet(sheet)
    app.setStyle(Q.QStyleFactory.create(style))
    app.setPalette(palette)
    if zh := getattr(app, "_zh", None):
        app.removeTranslator(zh)


def test_every_rule_is_scoped_so_the_overlay_is_untouched():
    css = theme.stylesheet()
    assert "%(" not in css
    selectors = [s.strip() for block in re.findall(r"([^{}]+)\{[^{}]*\}", css) for s in block.split(",")]
    assert len(selectors) > 40
    for s in selectors:
        assert s.startswith(("QMainWindow#main", "QDialog", "QMenu#tray", "QListView#heroPopup")), s


def test_icons_exist_in_every_scale():
    for url in set(re.findall(r'url\("([^"]+)"\)', theme.stylesheet())):
        assert "\\" not in url
        for suffix in ("", "@2x", "@3x"):
            assert not QImage(url.replace(".png", f"{suffix}.png")).isNull(), url


def test_look_does_not_depend_on_the_system_style(app):
    other = next((k for k in Q.QStyleFactory.keys() if k.lower() != "fusion"), None)
    if other:
        app.setStyle(other)
    theme.apply(app)
    assert app.style().objectName().lower() == "fusion" or app.style().metaObject().className() == "QStyleSheetStyle"
    assert app.palette().color(QPalette.Highlight) == QColor(theme.ACCENT)


def test_standard_buttons_are_chinese(app):
    theme.apply(app)
    box = Q.QMessageBox(Q.QMessageBox.Question, "t", "t", Q.QMessageBox.Yes | Q.QMessageBox.No | Q.QMessageBox.Ok | Q.QMessageBox.Cancel)
    assert {b.text() for b in box.buttons()} == {"是", "否", "确定", "取消"}


def test_overlay_is_pixel_identical(app, tmp_path, monkeypatch):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui.overlay import Overlay

    def shots():
        engine = Engine(Settings.load())
        ov = Overlay(engine)
        ov.timer.stop()
        app.processEvents()
        got = [w.grab().toImage() for w in (ov.panel.btn, ov.panel.body)]
        for w in (ov.panel, ov.badges, ov.champ):
            w.deleteLater()
        engine.stop()
        return got
    app.setStyleSheet("")
    app.setStyle("Fusion")  # 同一个底子下只看样式表有没有漏到浮窗上
    before = shots()
    theme.apply(app)
    assert shots() == before
