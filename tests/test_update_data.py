"""发版内置统计包与客户端遵循同样的来源和修订更新规则。"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from conftest import make_dataset

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("update_data", ROOT / "scripts" / "update_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _src(tmp_path, snapshot_at="2026-10-17T18:51:45+08:00", seq=12, changed=False):
    src = tmp_path / "src"
    folder = src / "patches" / "16.20"
    manifest = make_dataset(folder, snapshot_at=snapshot_at, seq=seq)
    if changed:
        heroes = json.loads((folder / "heroes.json").read_text(encoding="utf-8"))
        heroes["17"].update(win_rate=.51, wins=204000)
        body = json.dumps(heroes).encode("utf-8")
        (folder / "heroes.json").write_bytes(body)
        manifest["files"]["heroes.json"] = {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
    (src / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return src


def _setup(tmp_path, monkeypatch, snapshot="2026-10-17T18:51:45+08:00", seq=11):
    mod = _load()
    bundled = tmp_path / "bundled"
    make_dataset(bundled, snapshot_at=snapshot, seq=seq)
    monkeypatch.setattr(mod, "BUNDLED", bundled)
    return mod, bundled


def test_keeps_newer_bundled(tmp_path, monkeypatch, capsys):
    mod, bundled = _setup(tmp_path, monkeypatch, snapshot="2026-10-21T12:00:00+08:00")
    before = (bundled / "latest.json").read_bytes()
    assert mod.main(["--from", str(_src(tmp_path))]) == 0
    assert "不换" in capsys.readouterr().out
    assert (bundled / "latest.json").read_bytes() == before


def test_replaces_older_bundled(tmp_path, monkeypatch):
    mod, bundled = _setup(tmp_path, monkeypatch, snapshot="2026-10-16T09:15:50+08:00")
    assert mod.main(["--from", str(_src(tmp_path))]) == 0
    assert mod.Dataset(bundled, "bundled").seq == 12


@pytest.mark.parametrize("changed", [False, True])
def test_same_snapshot_new_seq_is_bundled(tmp_path, monkeypatch, changed):
    mod, bundled = _setup(tmp_path, monkeypatch)
    assert mod.main(["--from", str(_src(tmp_path, changed=changed))]) == 0
    assert mod.Dataset(bundled, "bundled").seq == 12
    if changed:
        assert mod.Dataset(bundled, "bundled").table(None)[0]["17"]["win_rate"] == .51


def test_same_seq_content_revision_is_bundled(tmp_path, monkeypatch):
    mod, bundled = _setup(tmp_path, monkeypatch)
    assert mod.main(["--from", str(_src(tmp_path, seq=11, changed=True))]) == 0
    assert mod.Dataset(bundled, "bundled").table(None)[0]["17"]["win_rate"] == .51


def test_old_seq_cannot_overwrite_new_bundled(tmp_path, monkeypatch):
    mod, bundled = _setup(tmp_path, monkeypatch, seq=13)
    before = (bundled / "latest.json").read_bytes()
    assert mod.main(["--from", str(_src(tmp_path, changed=True))]) == 0
    assert (bundled / "latest.json").read_bytes() == before


def test_bad_hash_leaves_reviewed_bundled_untouched(tmp_path, monkeypatch):
    mod, bundled = _setup(tmp_path, monkeypatch)
    before = (bundled / "latest.json").read_bytes()
    src = _src(tmp_path)
    (src / "patches" / "16.20" / "heroes.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="SHA-256"):
        mod.main(["--from", str(src)])
    assert (bundled / "latest.json").read_bytes() == before
    assert not bundled.with_name("bundled.new").exists()


def test_default_build_fetches_only_owned_servers_with_failover(tmp_path, monkeypatch):
    mod, bundled = _setup(tmp_path, monkeypatch)
    src = _src(tmp_path)
    monkeypatch.setattr(mod.cloud, "mirrors", lambda kind: ["https://owned.example/mirror"])
    monkeypatch.setattr(mod.cloud, "sites", lambda: ["https://owned.example"])
    seen = []

    def fetch(url):
        seen.append(url)
        if url.startswith("https://owned.example/mirror/"):
            raise OSError("mirror unavailable")
        prefix = "https://owned.example/u/data/"
        assert url.startswith(prefix), url
        return (src / url.removeprefix(prefix)).read_bytes()

    monkeypatch.setattr(mod, "_get", fetch)
    assert mod.main([]) == 0
    assert len(seen) > 3
    assert mod.Dataset(bundled, "bundled").seq == 12
