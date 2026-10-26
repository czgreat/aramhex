"""性能排查用的非身份信息：不读账号、设备序列号、主机名或环境变量。"""

from __future__ import annotations

import logging
import os
import platform
import time

log = logging.getLogger(__name__)
_STARTED = time.monotonic()


def runtime_context(settings) -> None:
    cpu = platform.processor() or "unknown"
    memory = "unknown"
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                cpu = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
        status = _system_memory()
        if status:
            memory = f"{status[0]}/{status[1]}MB"
    log.info("运行环境：系统=%s %s CPU=%s 逻辑核=%s 可用/总内存=%s 识别语言=%s 速度=%s 标题间隔=%s毫秒 启动扫描间隔=%s秒",
             platform.system(), platform.release(), cpu, os.cpu_count(), memory,
             settings.game_lang, settings.speed, settings.scan_interval_ms, settings.bootstrap_interval_s)


def _system_memory() -> tuple[int, int] | None:
    """系统可用/总内存（MB）；拿不到返回 None。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        *[(name, ctypes.c_ulonglong) for name in
                          ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.available // (1024**2), status.total // (1024**2)
    except (AttributeError, OSError):
        pass
    return None


def _process_memory() -> tuple[int, int, int] | None:
    """本进程 工作集/峰值工作集/私有提交（MB）；拿不到返回 None。只读计数器，不遍历内存。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD),
                        *[(name, ctypes.c_size_t) for name in
                          ("peak_ws", "ws", "peak_paged", "paged", "peak_nonpaged", "nonpaged",
                           "pagefile", "peak_pagefile", "private")]]
        c = Counters()
        c.cb = ctypes.sizeof(c)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        fn = getattr(k32, "K32GetProcessMemoryInfo", None) or ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD)
        if fn(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            mb = 1024**2
            return c.ws // mb, c.peak_ws // mb, c.private // mb
    except (AttributeError, OSError):
        pass
    return None


def resource_summary() -> str:
    """每局结束记一次：本程序已运行多久、占多少内存、系统还剩多少（排查开久了内存不够）。"""
    hours = (time.monotonic() - _STARTED) / 3600
    proc, system = _process_memory(), _system_memory()
    parts = [f"已运行={hours:.1f}小时"]
    if proc:
        parts.append(f"本程序内存={proc[0]}MB（峰值{proc[1]}MB，私有{proc[2]}MB）")
    if system:
        parts.append(f"系统可用/总内存={system[0]}/{system[1]}MB")
    return " ".join(parts)
