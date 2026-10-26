"""查找游戏 / 客户端窗口并取得客户区在屏幕上的物理像素坐标（Win32，只读）。"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
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
    """窗口在屏幕上的物理像素位置（含边框；无边框的悬浮层就是它画画的区域）。"""
    if not IS_WINDOWS or not hwnd:
        return None
    rc = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rc)):
        return None
    return Rect(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)


def set_window_rect(hwnd: int, r: Rect) -> None:
    """按物理像素摆放窗口（和截图同一套坐标），不改前后顺序、不抢焦点。"""
    if IS_WINDOWS and hwnd:
        # 0x0004 SWP_NOZORDER | 0x0010 SWP_NOACTIVATE | 0x0200 SWP_NOOWNERZORDER
        ctypes.windll.user32.SetWindowPos(wintypes.HWND(hwnd), None, r.left, r.top, r.width, r.height, 0x0214)


def dpi_info(hwnd: int = 0) -> str:
    """诊断用：窗口的 DPI 和它按哪种方式处理缩放；hwnd=0 时是本进程。拿不到返回空串。"""
    if not IS_WINDOWS:
        return ""
    u = ctypes.windll.user32
    try:
        u.GetAwarenessFromDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
        u.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
        u.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        ctx = u.GetWindowDpiAwarenessContext(wintypes.HWND(hwnd)) if hwnd else u.GetThreadDpiAwarenessContext()
        aw = {0: "不感知缩放", 1: "系统缩放", 2: "按显示器缩放"}.get(u.GetAwarenessFromDpiAwarenessContext(ctx), "?")
        dpi = u.GetDpiForWindow(wintypes.HWND(hwnd)) if hwnd else 0
        return f"{aw} DPI {dpi}" if dpi else aw
    except (AttributeError, OSError):
        return ""


# 贴在客户端/游戏上的自己的悬浮小窗：点它时前台变成它，但画面上客户端还在，按客户端仍在前台算
_companions: set[int] = set()


def add_companion(hwnd: int) -> None:
    if hwnd:
        _companions.add(int(hwnd))


def lower_thread_priority() -> None:
    """当前线程降到「低于正常」：游戏要用 CPU 时先让给游戏（只影响抢不到核的时候）。"""
    if not IS_WINDOWS:
        return
    try:
        k = ctypes.windll.kernel32
        k.GetCurrentThread.restype = wintypes.HANDLE
        k.SetThreadPriority.argtypes = (wintypes.HANDLE, ctypes.c_int)
        k.SetThreadPriority(k.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
    except (OSError, AttributeError):
        pass  # 降不了就按原样跑


# 最近一次在前台的、不是自己悬浮小窗的窗口。点浮窗之前在前台的是客户端/游戏，才按它仍在前台算；
# 之前在前台的是别的程序（客户端被它盖着），标注不能画到那个程序上面（10-25 172 选英雄截图）
_last_other = 0
_fg_hook = None


def _on_foreground(hwnd) -> None:
    global _last_other
    if hwnd and int(hwnd) not in _companions:
        _last_other = int(hwnd)


def watch_foreground() -> None:
    """订阅前台切换事件，每次切换都记下来：两次查询之间切到别的程序再点浮窗也不会漏。
    要在有消息循环的线程（界面线程）调用；装不上就只靠 is_foreground 查询时记。"""
    global _fg_hook
    if not IS_WINDOWS or _fg_hook is not None:
        return
    try:
        u = ctypes.windll.user32
        proc = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                  wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
        cb = proc(lambda _h, _ev, hwnd, *_: _on_foreground(hwnd))
        u.SetWinEventHook.restype = wintypes.HANDLE
        # EVENT_SYSTEM_FOREGROUND=3，WINEVENT_OUTOFCONTEXT=0：只收通知，不往别的进程里注入
        h = u.SetWinEventHook(3, 3, None, cb, 0, 0, 0)
        if h:
            _fg_hook = (h, cb)  # 回调要一直留着引用
        _on_foreground(u.GetForegroundWindow())
    except (OSError, AttributeError):
        pass


def is_foreground(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    fg = ctypes.windll.user32.GetForegroundWindow()
    if fg not in _companions:
        _on_foreground(fg)
        return fg == hwnd
    if ctypes.windll.user32.IsIconic(hwnd):
        return False
    from .. import cloud
    # 不知道点浮窗之前是哪个窗口时先不画（宁可少画一会儿，不画到别的程序上）
    return _last_other == hwnd or not cloud.feature("companion_last_fg")


# 本程序浮窗、标注当前画了东西的区域（屏幕物理像素）。识别截图后把这些地方涂掉，免得认到自己画的字。
# 录屏、直播能录到浮窗。{来源: (区域列表, 过期时刻 或 None)}；浮窗刚隐藏时多留一会儿，屏幕上可能还没消失
_drawn: dict[str, tuple[list[Rect], float | None]] = {}
_drawn_lock = threading.Lock()
DRAWN_LINGER_S = 0.5


def set_drawn(key: str, rects: list[Rect]) -> None:
    """登记某个浮窗现在画在哪（物理像素）；传空列表表示收起了。"""
    with _drawn_lock:
        if rects:
            _drawn[key] = (list(rects), None)
        elif key in _drawn and _drawn[key][1] is None:
            _drawn[key] = (_drawn[key][0], time.monotonic() + DRAWN_LINGER_S)


def drawn_rects() -> list[Rect]:
    now = time.monotonic()
    with _drawn_lock:
        for k in [k for k, (_, exp) in _drawn.items() if exp is not None and exp < now]:
            del _drawn[k]
        return [r for rects, _ in _drawn.values() for r in rects]


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
