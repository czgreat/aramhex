import os
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from lolhex import config


def test_concurrent_atomic_writes_use_distinct_temporary_files(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    replace = os.replace
    both_ready = threading.Barrier(2)
    attempts = threading.local()
    sources = []
    completed = []

    def replace_together(source, destination):
        # Windows 共享冲突重试时，另一个写入者已经可以结束。
        if not getattr(attempts, "synchronized", False):
            attempts.synchronized = True
            sources.append(source)
            both_ready.wait(timeout=5)
        replace(source, destination)
        completed.append(source)

    monkeypatch.setattr(config.os, "replace", replace_together)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda text: config.atomic_write_text(target, text), ["甲", "乙"]))

    assert len(set(sources)) == 2
    assert sorted(completed) == sorted(sources)
    assert target.read_text(encoding="utf-8") in {"甲", "乙"}
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_retries_windows_sharing_violation_and_cleans_up(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    target.write_text("旧", encoding="utf-8")
    replace = os.replace
    sleeps = []
    monkeypatch.setattr(config, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(config, "time", SimpleNamespace(sleep=sleeps.append))

    def sharing_violation(source, destination):
        if len(sleeps) < 2:
            raise PermissionError("sharing violation")
        replace(source, destination)

    monkeypatch.setattr(config.os, "replace", sharing_violation)
    config.atomic_write_text(target, "新")
    assert sleeps == [0.02, 0.04]
    assert target.read_text(encoding="utf-8") == "新"

    def permanently_locked(*args):
        raise PermissionError("sharing violation")

    monkeypatch.setattr(config.os, "replace", permanently_locked)
    with pytest.raises(PermissionError):
        config.atomic_write_text(target, "未写入")
    assert target.read_text(encoding="utf-8") == "新"
    assert list(tmp_path.iterdir()) == [target]
