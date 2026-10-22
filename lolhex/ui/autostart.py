"""开机自启：写入 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run（当前用户，不需要管理员）。"""

from __future__ import annotations

import sys

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "AramHex"


def _command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{exe}" -m lolhex --minimized'


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            winreg.QueryValueEx(k, NAME)
            return True
    except OSError:
        return False


def set_enabled(on: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg
    if on:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, _command())
    else:
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE)
        except FileNotFoundError:
            return  # 全新账号连 Run 键都没有：本来就没开自启，无需创建或报错
        with k:
            try:
                winreg.DeleteValue(k, NAME)
            except FileNotFoundError:
                pass
