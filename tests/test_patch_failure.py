"""增量更新的收尾：替换失败时下次启动能知道；替换脚本会强制结束卡住的旧进程、挪文件会重试。"""
import pytest

from lolhex import patch


def test_take_failure_reports_once_and_cleans(tmp_path):
    (tmp_path / patch.STAGING).mkdir()
    (tmp_path / "update-failed.txt").write_text("文件被占用", encoding="utf-8-sig")
    assert patch.take_failure(tmp_path) == "文件被占用"
    assert not (tmp_path / patch.STAGING).exists()
    assert patch.take_failure(tmp_path) is None


def test_take_failure_keeps_backup_only_when_rollback_incomplete(tmp_path):
    (tmp_path / patch.BACKUP).mkdir()
    (tmp_path / "update-failed.txt").write_text("被占用", encoding="utf-8-sig")
    (tmp_path / patch.ROLLBACK_INCOMPLETE).write_text("被占用", encoding="utf-8-sig")
    assert patch.rollback_incomplete(tmp_path)
    assert patch.take_failure(tmp_path) == "被占用"
    assert (tmp_path / patch.BACKUP).is_dir()
    (tmp_path / patch.ROLLBACK_INCOMPLETE).unlink()
    assert patch.take_failure(tmp_path) is None
    assert not (tmp_path / patch.BACKUP).exists()


def test_apply_script_kills_stuck_process_and_retries():
    s = patch.apply_script(patch.Path("C:/x"), ["a.exe"], [], 1234, "9.9.9", "C:/x/a.exe")
    assert "Stop-Process -Id $pid0" in s and "Move-Retry" in s


def test_rank_by_speed_puts_fastest_first(monkeypatch):
    import time

    class Resp:
        def __init__(self, url):
            self.slow = "slow" in url
            self.status_code = 206

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_content(self, n):
            for _ in range(8):
                if self.slow:
                    time.sleep(0.05)
                yield b"x" * n
    monkeypatch.setattr(patch.requests, "get", lambda url, **kw: Resp(url))
    assert patch.rank_by_speed(["https://slow.example", "https://fast.example"], "a.exe")[0] == "https://fast.example"


def test_apply_script_waits_about_a_minute_and_records_the_file():
    s = patch.apply_script(patch.Path("C:/x"), ["a.exe"], [], 1234, "9.9.9", "C:/x/a.exe")
    assert "$i -lt 30" in s and "Start-Sleep -Seconds 2" in s
    assert "$current = $rel" in s and "-Value $reason" in s


def test_powershell_full_path_only_after_not_found(monkeypatch, tmp_path):
    exe = tmp_path / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.setattr(patch.tempfile, "gettempdir", lambda: str(tmp_path))
    calls = []

    def normal(args, **k):
        calls.append(args[0])
    monkeypatch.setattr(patch.subprocess, "Popen", normal)
    patch.launch_apply(tmp_path, ["a.exe"], [], "9.9.9", pid=1)
    assert calls == ["powershell.exe"]  # 正常电脑：和以前完全一样

    calls.clear()

    def missing_first(args, **k):
        calls.append(args[0])
        if args[0] == "powershell.exe":
            raise FileNotFoundError(2, "系统找不到指定的文件。")
    monkeypatch.setattr(patch.subprocess, "Popen", missing_first)
    patch.launch_apply(tmp_path, ["a.exe"], [], "9.9.9", pid=1)
    assert calls == ["powershell.exe", str(exe)]


def test_prepare_stops_before_network_when_folder_not_writable(monkeypatch, tmp_path):
    def refuse(self, data):
        raise PermissionError("拒绝访问")
    monkeypatch.setattr(patch.Path, "write_bytes", refuse)
    monkeypatch.setattr(patch, "fetch_manifest", lambda *a, **k: pytest.fail("不该联网"))
    with pytest.raises(patch.NotWritable):
        patch.prepare("9.9.9", root=tmp_path)
