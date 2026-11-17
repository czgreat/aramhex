"""游戏前台时注册一个 Windows 快捷键；不安装键盘钩子，不轮询按键。"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence

log = logging.getLogger(__name__)
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
# 每代注册使用不同 id，避免释放前还没分发的 WM_HOTKEY 在同键重注册后误触发。
# 应用热键 id 上限为 0xBFFF；耗尽后停用并提示重启，不循环复用旧 id。
_ids = iter(range(0x4800, 0xC000))


def _next_id() -> int:
    try:
        return next(_ids)
    except StopIteration:
        raise OSError("快捷键注册次数已达本次运行上限，请重启助手。") from None


@dataclass(frozen=True)
class Shortcut:
    text: str
    modifiers: int
    key: int


def parse_shortcut(text: str) -> Shortcut | None:
    """只支持一个组合键，至少包含 Ctrl 或 Alt，避免接管普通游戏按键。"""
    if not isinstance(text, str):
        raise ValueError("快捷键格式无效，请重新录入。")
    if not text.strip():
        return None
    seq = QKeySequence.fromString(text, QKeySequence.PortableText)
    if seq.count() != 1:
        raise ValueError("请设置一个组合键，不支持连续多组按键。")
    combo = seq[0]
    mods = combo.keyboardModifiers()
    allowed = Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier
    if mods & ~allowed or not mods & (Qt.ControlModifier | Qt.AltModifier):
        raise ValueError("请包含 Ctrl 或 Alt，可再加 Shift；不支持 Win 键。")
    key = int(combo.key())
    if key == Qt.Key_F12:
        raise ValueError("F12 是 Windows 保留键，请选择 F1～F11 或字母、数字。")
    if Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
        vk = key
    elif Qt.Key_F1 <= key <= Qt.Key_F11:
        vk = 0x70 + key - int(Qt.Key_F1)
    else:
        raise ValueError("请使用 Ctrl / Alt 加字母、数字或 F1～F11，可再加 Shift。")
    modifiers = (0x0001 if mods & Qt.AltModifier else 0) | (0x0002 if mods & Qt.ControlModifier else 0)
    modifiers |= 0x0004 if mods & Qt.ShiftModifier else 0
    return Shortcut(seq.toString(QKeySequence.PortableText), modifiers, vk)


def _registration_error(code: int) -> str:
    if code == 1409:
        return "这个快捷键已被其他程序占用，请换一个组合键。"
    return f"Windows 无法启用此快捷键（错误 {code}），请换键后重试。"


class _NativeHotkey(QAbstractNativeEventFilter):
    def __init__(self, app, activated):
        super().__init__()
        self.app, self.activated = app, activated
        self.ident = 0
        self.probe_ident = _next_id()
        self.bound: Shortcut | None = None
        self.generation = 0
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
        self.api.RegisterHotKey.restype = wintypes.BOOL
        self.api.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        self.api.UnregisterHotKey.restype = wintypes.BOOL
        app.installNativeEventFilter(self)

    def _register(self, ident: int, shortcut: Shortcut) -> str:
        if self.api.RegisterHotKey(None, ident, shortcut.modifiers | MOD_NOREPEAT, shortcut.key):
            return ""
        return _registration_error(ctypes.get_last_error())

    def probe(self, shortcut: Shortcut) -> str:
        if shortcut == self.bound:
            return ""
        error = self._register(self.probe_ident, shortcut)
        if not error:
            self.api.UnregisterHotKey(None, self.probe_ident)
        return error

    def register(self, shortcut: Shortcut) -> str:
        self.release()
        try:
            self.ident = _next_id()
        except OSError as exc:
            return str(exc)
        error = self._register(self.ident, shortcut)
        if not error:
            self.bound = shortcut
        return error

    def release(self) -> None:
        self.generation += 1
        if self.bound is not None:
            self.api.UnregisterHotKey(None, self.ident)
            self.bound = None

    def nativeEventFilter(self, event_type, message):
        if event_type not in (b"windows_generic_MSG", b"windows_dispatcher_MSG") or not self.bound:
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        if msg.message != WM_HOTKEY or msg.wParam != self.ident:
            return False, 0
        # 改键前或释放后仍可能有排队的 WM_HOTKEY，不能当作新组合键处理。
        shortcut = self.bound
        generation = self.generation
        if msg.lParam & 0xFFFF == shortcut.modifiers and (msg.lParam >> 16) & 0xFFFF == shortcut.key:
            QTimer.singleShot(0, lambda: self.activated() if self.bound == shortcut and self.generation == generation else None)
        return True, 0

    def close(self) -> None:
        self.release()
        self.app.removeNativeEventFilter(self)


class GameHotkey(QObject):
    """注册随游戏前后台和浮窗总开关变化；注册失败不反复抢占组合键。"""

    activated = Signal()
    status_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.status = ""
        self._requested = None
        self._last_error = ""
        self.backend = None
        app = QGuiApplication.instance()
        # 离屏绘制 / 单元测试不能在开发机桌面注册全局按键。
        if sys.platform == "win32" and app and app.platformName() == "windows":
            try:
                self.backend = _NativeHotkey(app, self.activated.emit)
                app.aboutToQuit.connect(self.close)
            except (OSError, AttributeError):
                log.exception("初始化浮窗快捷键失败")

    def _status(self, text: str) -> None:
        if text != self.status:
            self.status = text
            self.status_changed.emit(text)

    def validate(self, text: str) -> str:
        try:
            shortcut = parse_shortcut(text)
        except ValueError as exc:
            return str(exc)
        return self.backend.probe(shortcut) if self.backend and shortcut else ""

    def sync(self, text: str, active: bool) -> None:
        request = (text, active)
        if request == self._requested:
            return
        if self._requested is None or text != self._requested[0]:
            self._last_error = ""
        self._requested = request
        if self.backend:
            self.backend.release()
        try:
            shortcut = parse_shortcut(text)
        except ValueError as exc:
            self._status(str(exc))
            return
        if shortcut is None:
            self._status("已禁用详情快捷键；海克斯推荐仍自动显示。")
        elif not self.backend:
            self._status("快捷键需要 Windows 桌面环境。")
        elif not active:
            self._status(self._last_error or f"{shortcut.text}：游戏或选英雄在前台时启用。")
        else:
            error = self.backend.register(shortcut)
            self._last_error = error
            self._status(error or f"{shortcut.text}：已启用，再按一次收起详情。")
            if error:
                log.warning("浮窗快捷键 %s：%s", shortcut.text, error)

    def close(self) -> None:
        if self.backend:
            self.backend.close()
            self.backend = None
        self._requested = None
