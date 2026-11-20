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


class _PowerThrottling(ctypes.Structure):
    _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]


# 识别线程优先级（设置 vision_priority）：below_normal 是 1.1.6 及以前、1.1.8 起的默认；lowest 是 1.1.7 的；
# normal 留给云控试：核少的电脑（如 4 核 4 线程）识别线程排不上队时，实际耗时远大于 CPU 时间
THREAD_PRIORITIES = {"normal": 0, "below_normal": -1, "lowest": -2}


def set_vision_thread(priority: str = "below_normal", eco: bool = False, no_throttle: bool = False) -> bool:
    """设置当前线程（识别线程）的优先级和效率模式，可以反复调用（云控改了就重设）：

    - 优先级按 THREAD_PRIORITIES。不用 IDLE：低配电脑游戏把核占满时，识别会一直排不上队、认不出海克斯。
    - eco=True 打开效率模式（EcoQoS，Windows 10 1709 起）：Intel 大小核上系统把它放到小核，其他 CPU 上降频跑。
      1.1.7 默认打开，Intel 12～14 代大小核电脑上每次认字的 CPU 时间涨到 2～3 倍、整块扫描慢 4～8 倍。
      eco=False 交还系统自己决定，和 1.1.6 没设置时一样；再加 no_throttle=True 是明确不要降频（系统的后台降频也不要）。
    返回效率模式是否打开（没要求打开、旧系统不支持时返回 False，照常运行）。"""
    if not IS_WINDOWS:
        return False
    try:
        k = ctypes.windll.kernel32
        k.GetCurrentThread.restype = wintypes.HANDLE
        k.SetThreadPriority.argtypes = (wintypes.HANDLE, ctypes.c_int)
        k.SetThreadPriority.restype = wintypes.BOOL
        k.SetThreadPriority(k.GetCurrentThread(), THREAD_PRIORITIES.get(priority, -1))
    except (OSError, AttributeError):
        pass  # 设不了就按原样跑；实际是多少用 vision_thread_priority 读回
    try:
        k = ctypes.windll.kernel32
        k.SetThreadInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k.SetThreadInformation.restype = wintypes.BOOL
        # THREAD_POWER_THROTTLING_CURRENT_VERSION；ControlMask/StateMask 都是 EXECUTION_SPEED = 打开，
        # ControlMask 是、StateMask 0 = 明确不降频，都是 0 = 交还系统
        state = _PowerThrottling(1, 1, 1) if eco else _PowerThrottling(1, 1, 0) if no_throttle else _PowerThrottling(1, 0, 0)
        ok = bool(k.SetThreadInformation(k.GetCurrentThread(), 3,  # ThreadPowerThrottling
                                         ctypes.byref(state), ctypes.sizeof(state)))
        return ok and eco
    except (OSError, AttributeError):
        return False


def vision_thread_priority() -> str | None:
    """当前线程实际的优先级（1.1.9）：THREAD_PRIORITIES 里的名字，别的值写数字；不是 Windows 或读不到返回 None。
    云控对照试验靠它确认设上了，不只看要求的值。"""
    if not IS_WINDOWS:
        return None
    try:
        k = ctypes.windll.kernel32
        k.GetCurrentThread.restype = wintypes.HANDLE
        k.GetThreadPriority.argtypes = (wintypes.HANDLE,)
        k.GetThreadPriority.restype = ctypes.c_int
        v = k.GetThreadPriority(k.GetCurrentThread())
    except (OSError, AttributeError):
        return None
    if v == 0x7FFFFFFF:  # THREAD_PRIORITY_ERROR_RETURN
        return None
    return next((name for name, p in THREAD_PRIORITIES.items() if p == v), str(v))


# 最近一次在前台的、不是自己悬浮小窗的窗口。点浮窗之前在前台的是客户端/游戏，才按它仍在前台算；
# 之前在前台的是别的程序（客户端被它盖着），标注不能画到那个程序上面
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


def describe(hwnd: int) -> str:
    """日志用：窗口类名，是本程序的窗口时注明。"""
    if not IS_WINDOWS or not hwnd:
        return str(hwnd or "无")
    u = ctypes.windll.user32
    buf = ctypes.create_unicode_buffer(128)
    u.GetClassNameW(hwnd, buf, 128)
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    own = "，本程序" if pid.value == os.getpid() else ""
    mark = "，浮窗" if int(hwnd) in _companions else ""
    return f"{buf.value or '?'}{own}{mark}"


def foreground_window() -> int:
    """现在的前台窗口；不是 Windows 为 0。"""
    if not IS_WINDOWS:
        return 0
    return int(ctypes.windll.user32.GetForegroundWindow() or 0)


def set_no_activate(hwnd: int, on: bool) -> bool:
    """窗口点了也不变成前台（WS_EX_NOACTIVATE，1.1.9）：鼠标点击、悬停照常收到，键盘一直归游戏。
    返回是否改成功；不是 Windows、窗口没了返回 False。"""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        u = ctypes.windll.user32
        if not u.IsWindow(hwnd):  # 窗口没了读出来是 0，会被当成「已清掉」
            return False
        get = getattr(u, "GetWindowLongPtrW", None) or u.GetWindowLongW
        put = getattr(u, "SetWindowLongPtrW", None) or u.SetWindowLongW
        get.restype = put.restype = ctypes.c_ssize_t
        get.argtypes = (wintypes.HWND, ctypes.c_int)
        put.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        old = get(hwnd, -20)  # GWL_EXSTYLE
        new = old | 0x08000000 if on else old & ~0x08000000
        if new != old:
            put(hwnd, -20, new)
        return bool(get(hwnd, -20) & 0x08000000) == on
    except (OSError, AttributeError):
        return False


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
