"""查找游戏 / 客户端窗口并取得客户区在屏幕上的物理像素坐标（Win32，只读）。"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass

GAME_CLASS = "RiotWindowClass"
GAME_TITLE = "League of Legends (TM) Client"
CLIENT_CLASS = "RCLIENT"

IS_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


def enable_dpi_awareness() -> None:
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _find(cls: str | None, title: str | None) -> int:
    if not IS_WINDOWS:
        return 0
    return ctypes.windll.user32.FindWindowW(cls, title) or 0


def find_game_window() -> int:
    return _find(GAME_CLASS, None) or _find(None, GAME_TITLE)


def find_client_window() -> int:
    return _find(CLIENT_CLASS, None)


def client_rect(hwnd: int) -> Rect | None:
    if not IS_WINDOWS or not hwnd:
        return None
    u = ctypes.windll.user32
    if u.IsIconic(hwnd) or not u.IsWindowVisible(hwnd):
        return None
    rc = wintypes.RECT()
    if not u.GetClientRect(hwnd, ctypes.byref(rc)):
        return None
    pt = wintypes.POINT(0, 0)
    u.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w < 200 or h < 150:
        return None
    return Rect(pt.x, pt.y, w, h)


def window_rect(hwnd: int) -> Rect | None:
    """整个窗口（含边框、标题栏）的屏幕坐标；和 client_rect 比能看出是窗口化还是无边框。"""
    if not IS_WINDOWS or not hwnd:
        return None
    rc = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc)):
        return None
    return Rect(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)


def window_dpi(hwnd: int) -> int | None:
    try:
        return int(ctypes.windll.user32.GetDpiForWindow(hwnd)) or None
    except Exception:
        return None


def process_path(hwnd: int) -> str | None:
    """窗口所属进程的 exe 路径（只读）。"""
    if not IS_WINDOWS or not hwnd:
        return None
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(len(buf))
        return buf.value if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else None
    finally:
        k.CloseHandle(h)


# 贴在客户端/游戏上的自己的悬浮小窗：点它时前台变成它，但画面上客户端还在，按客户端仍在前台算
_companions: set[int] = set()


def add_companion(hwnd: int) -> None:
    if hwnd:
        _companions.add(int(hwnd))


def is_foreground(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    fg = ctypes.windll.user32.GetForegroundWindow()
    return fg == hwnd or (fg in _companions and not ctypes.windll.user32.IsIconic(hwnd))


_overlay_windows: set[int] = set()
_capture_allowed = bool(os.environ.get("LOLHEX_DEBUG_CAPTURABLE"))  # 调试截图时让悬浮层可见


def exclude_from_capture(hwnd: int) -> None:
    """让自己的悬浮窗不出现在截图和录屏里（Win10 2004+）：一是避免识别到自己画的字，
    二是录视频、直播时画面干净。设置里打开"录屏、截图时显示浮窗"就不排除。"""
    if hwnd:
        _overlay_windows.add(int(hwnd))
        _apply_affinity(int(hwnd))


def _apply_affinity(hwnd: int) -> None:
    if IS_WINDOWS and hwnd:
        try:
            # 0x11 = WDA_EXCLUDEFROMCAPTURE；0 = 正常（能被截到）
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0 if _capture_allowed else 0x11)
        except Exception:
            pass


def set_capture_allowed(allowed: bool) -> None:
    """录屏、截图时是否显示浮窗；对已经打开的浮窗立即生效。"""
    global _capture_allowed
    _capture_allowed = bool(allowed) or bool(os.environ.get("LOLHEX_DEBUG_CAPTURABLE"))
    for hwnd in list(_overlay_windows):
        _apply_affinity(hwnd)


def capture_allowed() -> bool:
    return _capture_allowed


def give_focus(hwnd: int) -> None:
    """把前台（键盘焦点）还给游戏或客户端窗口。只有本进程当前在前台时 Windows 才允许，
    所以要在点了浮窗、浮窗刚被激活之后调用。不发送任何按键或鼠标。"""
    if not IS_WINDOWS or not hwnd:
        return
    u = ctypes.windll.user32
    if u.IsWindow(hwnd) and not u.IsIconic(hwnd) and u.GetForegroundWindow() != hwnd:
        u.SetForegroundWindow(hwnd)


def left_button_clicked() -> bool:
    """自上次调用以来左键是否按下过（GetAsyncKeyState 低位），只读输入状态。"""
    if not IS_WINDOWS:
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x0001)


def cursor_pos() -> tuple[int, int]:
    if not IS_WINDOWS:
        return (0, 0)
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y
