"""打包后的程序入口（PyInstaller）。

启动失败时（文件坏了、更新没装完整等）不弹英文的 "Unhandled exception"，改成中文提示，
引导去官网下载安装包覆盖安装；错误写进 crash.log 方便排查。这里尽量少依赖程序本身，程序坏了也能弹出来。
"""

import os
import sys
import time
import traceback

DOWNLOAD_PAGE = "https://github.com/czgreat/aramhex/releases"


def _crash_log(text: str) -> str:
    base = os.environ.get("ARAMHEX_HOME") or os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "AramHex")
    if os.path.isfile(os.path.join(os.path.dirname(sys.executable), "portable.txt")):
        base = os.path.join(os.path.dirname(sys.executable), "userdata")
    try:
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "crash.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{text}\n")
        return path
    except OSError:
        return ""


def _crash(text: str) -> None:
    path = _crash_log(text)
    msg = ("海克斯大乱斗助手打不开了。\n\n"
           "请重新下载安装包，直接覆盖安装就行（设置会保留）。\n\n"
           "点「是」打开下载页。")
    if path:
        msg += f"\n\n错误记录：{path}"
    try:
        import ctypes
        if ctypes.windll.user32.MessageBoxW(None, msg, "海克斯大乱斗助手", 0x4 | 0x10) == 6:  # 是/否 + 错误图标；6 = 是
            import webbrowser
            webbrowser.open(DOWNLOAD_PAGE)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        from lolhex.__main__ import main
        code = main()
    except SystemExit:
        raise
    except BaseException:
        _crash(traceback.format_exc())
        code = 1
    sys.exit(code)
