"""Full-review regressions: Windows paths and damaged downloaded statistics."""
import hashlib
import json

import pytest

from conftest import make_dataset
from lolhex.data import public


@pytest.mark.parametrize("name", [
    "C:/outside.json", r"C:\outside.json", "C:outside.json", r"bands\high\heroes.json",
    "bands//heroes.json", "bands/./heroes.json", "bands/high /heroes.json",
    "bands/high./heroes.json", "bands/%2e%2e/heroes.json", "NUL.json", "x:stream.json",
])
def test_windows_manifest_paths_rejected_before_network(tmp_path, monkeypatch, name):
    manifest = make_dataset(tmp_path / "source", bands=False)
    manifest["files"][name] = {"sha256": "0" * 64, "size": 0}
    calls = []
    monkeypatch.setattr(public.http, "fetch", lambda *a, **k: calls.append(a))
    with pytest.raises(public.DatasetError):
        public.download("https://example.invalid", manifest, tmp_path / "public")
    assert calls == [] and not (tmp_path / "public.new").exists()


def test_absolute_manifest_never_writes_outside_staging(tmp_path, monkeypatch):
    source = tmp_path / "source"
    manifest = make_dataset(source, bands=False)
    outside = tmp_path / "outside.json"
    original = b'{"keep":true}'
    outside.write_bytes(original)
    body = b'{"overwritten":true}'
    manifest["files"][str(outside)] = {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
    def fetch(url, **kwargs):
        if url.endswith(".pack.xz"):
            raise OSError("Individual-file fixture")
        return body if url.endswith(str(outside)) else (source / url.rsplit("/", 1)[-1]).read_bytes()
    monkeypatch.setattr(public.http, "fetch", fetch)
    with pytest.raises(public.DatasetError):
        public.download("https://example.invalid", manifest, tmp_path / "public")
    assert outside.read_bytes() == original


def test_linked_dataset_file_cannot_escape_directory(tmp_path):
    root, other = tmp_path / "data", tmp_path / "outside.json"
    make_dataset(root, bands=False)
    target = root / "heroes.json"
    other.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(other)
    except OSError:
        pytest.skip("File symlinks unavailable for this Windows account")
    with pytest.raises(public.DatasetError):
        public.Dataset(root, "downloaded")


def test_overflowing_cached_count_falls_back_instead_of_aborting_startup(tmp_path):
    bundled, downloaded = tmp_path / "bundled", tmp_path / "data" / "public"
    make_dataset(bundled, bands=False)
    m = make_dataset(downloaded, bands=False, snapshot_at="2026-10-29T00:00:00+08:00")
    p = downloaded / "heroes.json"
    rows = json.loads(p.read_text(encoding="utf-8"))
    rows["17"]["games"] = 10**400
    body = json.dumps(rows).encode("utf-8")
    p.write_bytes(body)
    m["files"]["heroes.json"] = {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
    (downloaded / "latest.json").write_text(json.dumps(m), encoding="utf-8")
    store = public.PublicStore(downloaded.parent, bundled=bundled, base_url=[], managed_official=True)
    assert store.dataset.origin == "bundled" and store.win_data("17")
