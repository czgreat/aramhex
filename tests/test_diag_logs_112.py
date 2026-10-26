"""1.1.2 新增诊断日志：每局一次的运行资源、未识别卡的原文。都不改识别和推荐。"""

import os

import pytest

from lolhex import diagnostics
from lolhex.engine import _unread


def test_unread_shows_trimmed_ocr_text():
    offer = {"texts": ["重型（强化 Q）", "力量（强化\nW）多余的很长很长很长的文字", ""]}
    assert _unread(offer, 1) == "未识别「力量（强化 W）多余的很长很长很」"
    assert _unread(offer, 2) == "未识别"
    assert _unread({}, 0) == "未识别"


def test_resource_summary_has_uptime():
    s = diagnostics.resource_summary()
    assert s.startswith("已运行=") and "小时" in s


@pytest.mark.skipif(os.name != "nt", reason="只在 Windows 读进程计数器")
def test_process_memory_reads_on_windows():
    ws, peak, private = diagnostics._process_memory()
    assert 0 < ws <= peak and private > 0
    assert "本程序内存=" in diagnostics.resource_summary()
