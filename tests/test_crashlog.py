"""闪退后下次启动能在普通日志里看到调用栈。崩溃在子进程里做，不影响测试进程。"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _run(code, home):
    return subprocess.run([sys.executable, "-c", code, str(home)], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60, env={**os.environ, "PYTHONUTF8": "1"})


def test_crash_is_reported_once_on_next_start(tmp_path):
    crash = ("import faulthandler, sys\nfrom lolhex import crashlog\n"
             "crashlog.enable(sys.argv[1])\nfaulthandler._sigsegv()\n")
    assert _run(crash, tmp_path).returncode != 0
    assert (tmp_path / "crash.log").stat().st_size > 0
    start = ("import logging, sys\nfrom lolhex import crashlog\n"
             "logging.basicConfig(stream=sys.stdout, level=logging.INFO)\ncrashlog.enable(sys.argv[1])\n")
    first = _run(start, tmp_path).stdout
    assert "上次运行记录到底层异常" in first and "File \"<string>\"" in first
    assert (tmp_path / "crash.log").stat().st_size == 0
    assert "底层异常" not in _run(start, tmp_path).stdout  # 只报一次


def test_unwritable_home_does_not_stop_startup(tmp_path):
    code = ("import logging, sys\nfrom lolhex import crashlog\n"
            "logging.basicConfig(stream=sys.stdout, level=logging.INFO)\ncrashlog.enable(sys.argv[1])\nprint('ok')\n")
    assert "ok" in _run(code, tmp_path / "没有这个目录").stdout


def test_stops_recording_past_the_limit(tmp_path):
    code = ("import faulthandler, sys\nfrom lolhex import crashlog\ncrashlog.MAX_FILE_BYTES = 100\n"
            "crashlog.enable(sys.argv[1])\ncrashlog.enforce_limit()\nassert faulthandler.is_enabled()\n"
            "crashlog._file.write('x' * 101)\ncrashlog._file.flush()\ncrashlog.enforce_limit()\n"
            "assert not faulthandler.is_enabled() and crashlog._file is None\ncrashlog.enforce_limit()\nprint('ok')\n")
    r = _run(code, tmp_path)
    assert "ok" in r.stdout, r.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="可恢复的底层异常只有 Windows 上会记录")
def test_handled_native_exceptions_cannot_grow_the_file_forever(tmp_path):
    """库自己恢复的访问异常反复出现，进程不退出：到上限后文件不再变大。"""
    code = """
import ctypes, sys
from ctypes import wintypes as W
from pathlib import Path
from lolhex import crashlog
k = ctypes.WinDLL("kernel32")
CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
keep_going = CALLBACK(lambda info: -1)
k.AddVectoredExceptionHandler.argtypes = [W.ULONG, CALLBACK]
k.AddVectoredExceptionHandler.restype = ctypes.c_void_p
assert k.AddVectoredExceptionHandler(0, keep_going)
k.RaiseException.argtypes = [W.DWORD, W.DWORD, W.DWORD, ctypes.c_void_p]
crashlog.MAX_FILE_BYTES = 2000
crashlog.enable(sys.argv[1])
path = Path(sys.argv[1]) / "crash.log"
for _ in range(100):
    k.RaiseException(0xC0000005, 0, 0, None)
    crashlog.enforce_limit()
assert crashlog._file is None
size = path.stat().st_size
assert size > 2000
for _ in range(20):
    k.RaiseException(0xC0000005, 0, 0, None)
assert path.stat().st_size == size
print("ok")
"""
    r = _run(code, tmp_path)
    assert "ok" in r.stdout, r.stderr
