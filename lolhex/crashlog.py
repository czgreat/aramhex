"""原生崩溃记录：闪退时 Python 日志来不及写，由 faulthandler 把各线程调用栈写进 crash.log。

下次启动时转进普通日志（点名上传只收普通日志，上传前统一脱敏），然后清空。
Windows 上被库自己处理掉的底层异常也可能留下记录，所以这里只说“记录到”，不直接等于闪退。
这种异常可能反复出现，而 faulthandler 不管文件多大，所以由界面定时器顺带查大小，超过上限就停止记录。
"""

from __future__ import annotations

import faulthandler
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)
MAX_BYTES = 4000
MAX_FILE_BYTES = 1024 * 1024
_file = None  # 进程结束前一直开着：崩溃时 faulthandler 直接往这个句柄写


def enable(home: Path) -> None:
    global _file
    path = Path(home) / "crash.log"
    try:
        if path.exists():
            with open(path, "rb") as f:
                f.seek(max(0, path.stat().st_size - MAX_BYTES))
                text = f.read().decode("utf-8", "replace").strip()
            if text:
                log.warning("上次运行记录到底层异常（可能已闪退）：\n%s", text)
        _file = open(path, "w", encoding="utf-8")
        faulthandler.enable(file=_file, all_threads=True)
    except (OSError, ValueError, RuntimeError) as e:
        log.info("崩溃记录没有打开：%s", e)


def enforce_limit() -> None:
    global _file
    if _file is None:
        return
    try:
        if os.fstat(_file.fileno()).st_size <= MAX_FILE_BYTES:
            return
        faulthandler.disable()
        _file.close()
    except (OSError, ValueError) as e:
        log.info("崩溃记录没有关掉：%s", e)
    _file = None
    log.warning("底层异常记录超过 %d 字节，这次运行不再记录", MAX_FILE_BYTES)
