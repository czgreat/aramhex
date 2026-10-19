"""同一个 Windows 用户只能运行一个助手。

以前只靠本地套接字（QLocalServer）：第二个进程连不上第一个时，就当没人在跑，自己再起一个。
Windows 上同名管道可以有多个服务端，第一个进程忙、半秒内没接上连接时就会出现两个助手。

现在用命名互斥量判断"有没有人在跑"，只有一个进程能拿到；套接字只用来通知已有的助手显示主窗口。
"""

from __future__ import annotations

import ctypes
import sys

WAIT_OBJECT_0, WAIT_ABANDONED = 0x0, 0x80
_held: list = []  # 拿到的互斥量句柄：进程退出时由系统释放
denied = False  # 已有的助手是以管理员身份运行的，这个进程没权限打开它的互斥量
ERROR_ACCESS_DENIED = 5


def acquire(name: str, wait_ms: int = 0) -> bool:
    """拿到名为 name 的互斥量返回 True（本进程就是唯一的助手）。等 wait_ms 毫秒还拿不到返回 False。
    非 Windows 或系统调用失败时返回 True：判断不了就照常启动，不能让助手打不开。
    例外是「没有权限」：已有的助手是右键以管理员身份打开的，普通方式再打开就打不开它的互斥量。
    以前这也当成判断不了，结果会开出第二个助手。现在算已经有人在跑（denied 记下来）。"""
    global denied
    if sys.platform != "win32":
        return True
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = ctypes.c_void_p
    k.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    k.WaitForSingleObject.restype = ctypes.c_uint32
    k.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    if not _held:
        h = k.CreateMutexW(None, False, "Local\\" + name)
        if not h:
            if ctypes.get_last_error() == ERROR_ACCESS_DENIED:
                denied = True
                return False
            return True
        _held.append(h)
    # 互斥量归拿到它的线程所有：在主线程调用，主线程一直活到进程结束
    return k.WaitForSingleObject(_held[0], max(0, int(wait_ms))) in (WAIT_OBJECT_0, WAIT_ABANDONED)
