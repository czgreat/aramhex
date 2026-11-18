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
    log.info("运行环境：系统=%s %s CPU=%s 逻辑核=%s 大小核=%s 可用/总内存=%s 识别语言=%s 速度=%s 标题间隔=%s毫秒 启动扫描间隔=%s秒",
             platform.system(), platform.release(), cpu, os.cpu_count(), core_classes() or "unknown", memory,
             settings.game_lang, settings.speed, settings.scan_interval_ms, settings.bootstrap_interval_s)


def core_classes() -> str:
    """大小核（1.1.8）：按 Windows CPU Set 的 EfficiencyClass 数逻辑核，大核在前，如「P16+E8」；
    只有一种核时为「否」；拿不到（Windows 10 以前、非 Windows）为空。"""
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        k.GetSystemCpuSetInformation.argtypes = (ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
                                                 wintypes.HANDLE, wintypes.ULONG)
        k.GetSystemCpuSetInformation.restype = wintypes.BOOL
        need = wintypes.ULONG(0)
        k.GetSystemCpuSetInformation(None, 0, ctypes.byref(need), None, 0)
        if not need.value:
            return ""
        buf = ctypes.create_string_buffer(need.value)
        if not k.GetSystemCpuSetInformation(buf, need, ctypes.byref(need), None, 0):
            return ""
        return format_core_classes(cpu_set_classes(buf.raw[:need.value]))
    except (AttributeError, OSError, ValueError):
        return ""


def cpu_set_classes(raw: bytes) -> dict[int, int]:
    """SYSTEM_CPU_SET_INFORMATION 数组 → {EfficiencyClass: 逻辑核数}。每条开头是 Size、Type（0 = CpuSet），
    EfficiencyClass 在第 18 字节。"""
    out: dict[int, int] = {}
    pos = 0
    while pos + 19 <= len(raw):
        size = int.from_bytes(raw[pos:pos + 4], "little")
        if size < 19:
            break
        if int.from_bytes(raw[pos + 4:pos + 8], "little") == 0:
            cls = raw[pos + 18]
            out[cls] = out.get(cls, 0) + 1
        pos += size
    return out


def format_core_classes(classes: dict[int, int]) -> str:
    if not classes:
        return ""
    if len(classes) == 1:
        return "否"
    counts = [classes[c] for c in sorted(classes, reverse=True)]  # EfficiencyClass 大的是大核
    return "+".join(f"{'P' if i == 0 else 'E'}{n}" for i, n in enumerate(counts))


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
