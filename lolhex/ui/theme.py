"""主界面的外观：自己画，Win10、Win11 一个样。

以前没有指定样式，Qt 按系统挑：Win11 用 windows11，Win10 退回 windowsvista（按钮、下拉框、标签页都是 Vista 的老样子）。
现在统一用 Qt 自带的 Fusion 打底，再用样式表定颜色、圆角和间距，不依赖系统主题。

只管主窗口、对话框、托盘菜单和提示框：选择器都限定在 QMainWindow#main / QDialog 里面，
游戏里的浮窗（overlay.py）有自己的深色样式，这里一条都不能落到它身上。
"""

from __future__ import annotations

from importlib import resources

from PySide6.QtCore import Qt, QTranslator
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory, QWidget

BG = "#f2f4f8"
CARD = "#ffffff"
LINE = "#e1e6ee"
FIELD = "#cfd6e2"
TEXT = "#1d2433"
MUTED = "#667085"
ACCENT = "#2f6bd8"
ACCENT_DARK = "#2558b8"
GOOD = "#1f8f50"
BAD = "#cf4436"
# 有的 Win10（精简系统、英文系统）界面字体是宋体或没有中文的 Segoe UI，所以写死顺序
FONTS = ("Microsoft YaHei UI", "Microsoft YaHei", "Microsoft JhengHei UI", "Segoe UI")

_QSS = """
QMainWindow#main, QDialog { background: %(BG)s; }
QMainWindow#main QWidget, QDialog QWidget, QMenu#tray { font-family: %(FONTS)s; font-size: 13px; }
QMainWindow#main QWidget#page, QMainWindow#main QScrollArea, QMainWindow#main QWidget#qt_scrollarea_viewport { background: transparent; }
QMainWindow#main QLabel, QDialog QLabel, QMainWindow#main QCheckBox { color: %(TEXT)s; background: transparent; }
QMainWindow#main QLabel[muted="true"] { color: %(MUTED)s; }
QMainWindow#main QFrame#card { background: %(CARD)s; border: 1px solid %(LINE)s; border-radius: 10px; }
QMainWindow#main QFrame#footer { background: #f7f9fc; border: 1px solid %(LINE)s; border-radius: 8px; }
QMainWindow#main QFrame#notice { background: #fff7df; border: 1px solid #ecd08a; border-radius: 10px; }

QMainWindow#main QPushButton, QDialog QPushButton {
    background: %(CARD)s; color: %(TEXT)s; border: 1px solid %(FIELD)s; border-radius: 6px; padding: 4px 14px; min-height: 18px; }
QMainWindow#main QPushButton:hover, QDialog QPushButton:hover { background: #f4f7fd; border-color: %(ACCENT)s; color: %(ACCENT)s; }
QMainWindow#main QPushButton:pressed, QDialog QPushButton:pressed { background: #e6eefb; }
QMainWindow#main QPushButton:focus, QDialog QPushButton:focus { border-color: %(ACCENT)s; }
QMainWindow#main QPushButton:disabled, QDialog QPushButton:disabled { background: #f3f4f7; color: #a3abb9; border-color: %(LINE)s; }
QMainWindow#main QPushButton[primary="true"], QDialog QPushButton:default {
    background: %(ACCENT)s; color: white; border-color: %(ACCENT)s; }
QMainWindow#main QPushButton[primary="true"]:hover, QDialog QPushButton:default:hover { background: %(ACCENT_DARK)s; border-color: %(ACCENT_DARK)s; color: white; }
QMainWindow#main QPushButton[primary="true"]:disabled { background: #a9c1ee; border-color: #a9c1ee; color: white; }
QDialog QPushButton { min-width: 64px; }

QMainWindow#main QLineEdit, QMainWindow#main QComboBox, QDialog QLineEdit {
    background: %(CARD)s; color: %(TEXT)s; border: 1px solid %(FIELD)s; border-radius: 6px; padding: 3px 9px; min-height: 20px;
    selection-background-color: %(ACCENT)s; selection-color: white; }
QMainWindow#main QLineEdit:hover, QMainWindow#main QComboBox:hover { border-color: #9fb0cb; }
QMainWindow#main QLineEdit:focus, QMainWindow#main QComboBox:focus, QMainWindow#main QComboBox:on, QDialog QLineEdit:focus { border-color: %(ACCENT)s; }
QMainWindow#main QLineEdit:disabled, QMainWindow#main QComboBox:disabled { background: #f3f4f7; color: #a3abb9; }
QMainWindow#main QKeySequenceEdit { background: transparent; border: none; }
QMainWindow#main QComboBox { padding-right: 26px; combobox-popup: 0; }
QMainWindow#main QComboBox::drop-down { subcontrol-origin: border; subcontrol-position: center right; width: 26px; border: none; }
QMainWindow#main QComboBox::down-arrow { image: url("%(CHEVRON)s"); width: 12px; height: 12px; }
QMainWindow#main QComboBox QAbstractItemView, QListView#heroPopup {
    background: %(CARD)s; color: %(TEXT)s; border: 1px solid %(FIELD)s; padding: 4px; outline: none;
    font-family: %(FONTS)s; font-size: 13px; }
QMainWindow#main QComboBox QAbstractItemView::item, QListView#heroPopup::item { min-height: 26px; padding: 0 8px; border-radius: 4px; color: %(TEXT)s; }
QMainWindow#main QComboBox QAbstractItemView::item:hover, QListView#heroPopup::item:hover { background: #eef3fc; }
QMainWindow#main QComboBox QAbstractItemView::item:selected, QListView#heroPopup::item:selected { background: #e1ebfb; color: %(ACCENT_DARK)s; }

QMainWindow#main QCheckBox { spacing: 8px; }
QMainWindow#main QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #b4bfd0; border-radius: 4px; background: %(CARD)s; }
QMainWindow#main QCheckBox::indicator:hover { border-color: %(ACCENT)s; }
QMainWindow#main QCheckBox::indicator:checked { background: %(ACCENT)s; border-color: %(ACCENT)s; image: url("%(CHECK)s"); }
QMainWindow#main QCheckBox::indicator:checked:hover { background: %(ACCENT_DARK)s; }
QMainWindow#main QCheckBox::indicator:disabled { background: #e9ecf1; border-color: %(LINE)s; }

QMainWindow#main QTabWidget::pane { border: 1px solid %(LINE)s; border-radius: 10px; background: %(CARD)s; top: 6px; margin-bottom: 6px; }
QMainWindow#main QTabWidget::tab-bar { left: 2px; }
QMainWindow#main QTabBar { background: transparent; qproperty-drawBase: 0; }
QMainWindow#main QTabBar::tab { background: transparent; color: %(MUTED)s; border: none; border-radius: 6px; padding: 6px 16px; margin-right: 4px; min-height: 18px; }
QMainWindow#main QTabBar::tab:hover { background: #e7ecf5; color: %(TEXT)s; }
QMainWindow#main QTabBar::tab:selected { background: %(ACCENT)s; color: white; }
QMainWindow#main QTabBar::tab:focus { border: 1px solid %(ACCENT_DARK)s; }

QMainWindow#main QProgressBar { background: #e8ecf3; border: none; border-radius: 9px; color: %(TEXT)s; text-align: center; font-size: 12px; }
QMainWindow#main QProgressBar::chunk { background: #a8c4f5; border-radius: 9px; }

QMainWindow#main QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QMainWindow#main QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QMainWindow#main QScrollBar::handle { background: #c6cedb; border-radius: 3px; }
QMainWindow#main QScrollBar::handle:vertical { min-height: 28px; }
QMainWindow#main QScrollBar::handle:horizontal { min-width: 28px; }
QMainWindow#main QScrollBar::handle:hover { background: #a5b0c2; }
QMainWindow#main QScrollBar::add-line, QMainWindow#main QScrollBar::sub-line { width: 0; height: 0; }
QMainWindow#main QScrollBar::add-page, QMainWindow#main QScrollBar::sub-page { background: transparent; }

QMainWindow#main QAbstractScrollArea::corner { background: transparent; }

QMenu#tray { background: %(CARD)s; color: %(TEXT)s; border: 1px solid %(FIELD)s; padding: 5px; }
QMenu#tray::item { padding: 7px 26px 7px 30px; border-radius: 4px; }
QMenu#tray::item:selected { background: #e1ebfb; color: %(ACCENT_DARK)s; }
QMenu#tray::separator { height: 1px; background: %(LINE)s; margin: 5px 8px; }
QMenu#tray::indicator { width: 14px; height: 14px; left: 9px; border: 1px solid #b4bfd0; border-radius: 3px; background: %(CARD)s; }
QMenu#tray::indicator:checked { background: %(ACCENT)s; border-color: %(ACCENT)s; image: url("%(CHECK)s"); }
"""


def asset(name: str) -> str:
    return str(resources.files("lolhex.ui").joinpath(name)).replace("\\", "/")


def stylesheet() -> str:
    return _QSS % {**{k: v for k, v in globals().items() if k.isupper() and isinstance(v, str)},
                   "FONTS": ", ".join(f'"{f}"' for f in FONTS),
                   "CHECK": asset("check.png"), "CHEVRON": asset("chevron.png")}


def palette() -> QPalette:
    """样式表没管到的地方（链接、选中文字、占位字）也用同一套颜色，不跟系统的强调色走。"""
    p = QStyleFactory.create("Fusion").standardPalette()
    for role, color in ((QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Text, TEXT), (QPalette.ButtonText, TEXT),
                        (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#ffffff"), (QPalette.Accent, ACCENT),
                        (QPalette.Link, ACCENT), (QPalette.LinkVisited, ACCENT), (QPalette.PlaceholderText, "#98a2b3")):
        p.setColor(role, QColor(color))
    return p


# 安装包不带 Qt 的翻译文件：对话框按钮、输入框右键菜单本来是英文
_ZH = {"OK": "确定", "&OK": "确定", "Cancel": "取消", "&Cancel": "取消", "&Yes": "是", "&No": "否", "Close": "关闭", "&Close": "关闭",
       "&Undo": "撤销", "&Redo": "重做", "Cu&t": "剪切", "&Copy": "复制", "&Paste": "粘贴", "Delete": "删除", "Select All": "全选",
       "Copy &Link Location": "复制链接地址"}
_ZH_CONTEXTS = {"QPlatformTheme", "QDialogButtonBox", "QLineEdit", "QWidgetTextControl"}


class _Zh(QTranslator):
    def isEmpty(self) -> bool:
        return False

    def translate(self, context, text, disambiguation=None, n=-1):
        return _ZH.get(text) if context in _ZH_CONTEXTS else None


def apply(app: QApplication) -> None:
    """启动时调一次，在建任何窗口之前。"""
    # 颜色按浅色配的：不跟随 Windows 深色模式，否则白字压在浅色卡片上看不见
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(palette())
    app.setStyleSheet(stylesheet())
    zh = _Zh(app)
    app.installTranslator(zh)
    app._zh = zh  # 保持引用


def mark(widget: QWidget, **props) -> QWidget:
    """设样式表用的属性（primary、muted），返回控件本身。"""
    for name, value in props.items():
        widget.setProperty(name, value)
    return widget
