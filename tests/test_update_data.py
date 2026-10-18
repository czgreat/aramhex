"""发版时取最新统计数据：内置数据更新（例如过渡数据）时不能被覆盖。"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("update_data", ROOT / "scripts" / "update_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _src(tmp_path, snapshot_at):
    src = tmp_path / "src"
    src.mkdir()
    (src / "latest.json").write_text(json.dumps({"snapshot_at": snapshot_at, "path": "x", "files": {}}), encoding="utf-8")
    return src


def test_keeps_newer_bundled(tmp_path, monkeypatch, capsys):
    mod = _load()
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "latest.json").write_text(json.dumps({"snapshot_at": "2026-10-21T12:00:00+08:00"}), encoding="utf-8")
    monkeypatch.setattr(mod, "BUNDLED", bundled)
    monkeypatch.setattr(mod, "check_manifest", lambda m: m)
    assert mod.main(["--from", str(_src(tmp_path, "2026-10-17T18:51:45+08:00"))]) == 0
    assert "不换" in capsys.readouterr().out
    assert json.loads((bundled / "latest.json").read_text(encoding="utf-8"))["snapshot_at"].startswith("2026-10-21")


def test_replaces_older_bundled(tmp_path, monkeypatch):
    mod = _load()
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "latest.json").write_text(json.dumps({"snapshot_at": "2026-10-16T09:15:50+08:00"}), encoding="utf-8")
    monkeypatch.setattr(mod, "BUNDLED", bundled)
    monkeypatch.setattr(mod, "check_manifest", lambda m: m)
    monkeypatch.setattr(mod, "Dataset", lambda path, name: type("D", (), {"patch": "16.20", "snapshot_at": "x"})())
    assert mod.main(["--from", str(_src(tmp_path, "2026-10-17T18:51:45+08:00"))]) == 0
    assert json.loads((bundled / "latest.json").read_text(encoding="utf-8"))["snapshot_at"].startswith("2026-10-17")
