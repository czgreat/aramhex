"""A valid digest does not make malformed statistics safe to activate."""
import hashlib
import json
import shutil

import pytest

from conftest import make_dataset
from lolhex.data import public


def rewrite(folder, name, value):
    body = json.dumps(value).encode("utf-8")
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    manifest = json.loads((folder / "latest.json").read_text(encoding="utf-8"))
    manifest["files"][name] = {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
    (folder / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


@pytest.mark.parametrize("name,value", [
    ("heroes.json", [1]), ("heroes.json", None), ("heroes.json", {}),
    ("heroes.json", {"bad-id": {"games": 10, "wins": 5, "win_rate": .5}}),
    ("heroes.json", {"17": []}), ("heroes.json", {"17": {"games": 10}}),
    ("hero_augments.json", []), ("hero_augments.json", {"17": []}),
    ("hero_augments.json", {"17": {"bad-id": {"games": 10, "wins": 5, "win_rate": .5}}}),
    ("bands/high/heroes.json", [1]), ("bands/high/hero_augments.json", {"17": [1]}),
    ("hero_spells.json", [1]), ("hero_spells.json", {"17": [1]}),
    ("hero_items.json", {"17": "bad"}), ("bands/high/hero_spells.json", {"17": [1]}), ("meta.json", []),
])
def test_semantic_corruption_rejected_before_use(tmp_path, name, value):
    make_dataset(tmp_path)
    rewrite(tmp_path, name, value)
    with pytest.raises(public.DatasetError):
        public.Dataset(tmp_path, "downloaded")


@pytest.mark.parametrize("field,value", [
    ("games", "10"), ("games", True), ("games", -1), ("games", 0),
    ("wins", None), ("wins", False), ("wins", -1), ("wins", 11),
    ("win_rate", "0.5"), ("win_rate", True), ("win_rate", float("nan")),
    ("win_rate", float("inf")), ("win_rate", -.1), ("win_rate", 1.1),
])
def test_invalid_required_numbers_rejected(tmp_path, field, value):
    make_dataset(tmp_path)
    row = {"games": 10, "wins": 5, "win_rate": .5}
    row[field] = value
    rewrite(tmp_path, "heroes.json", {"17": row})
    with pytest.raises(public.DatasetError):
        public.Dataset(tmp_path, "downloaded")


def test_partial_band_pair_rejected(tmp_path):
    make_dataset(tmp_path, bands=False)
    rewrite(tmp_path, "bands/high/heroes.json", {"17": {"games": 10, "wins": 5, "win_rate": .5}})
    with pytest.raises(public.DatasetError):
        public.Dataset(tmp_path, "downloaded")


def test_bad_download_keeps_previous_data_and_restart_works(tmp_path, monkeypatch):
    remote, dest = tmp_path / "remote", tmp_path / "data" / "public"
    make_dataset(dest, snapshot_at="2026-10-20T00:00:00+08:00")
    make_dataset(remote, snapshot_at="2026-10-28T00:00:00+08:00")
    manifest = rewrite(remote, "heroes.json", [1])
    previous = (dest / "latest.json").read_bytes()

    def fetch(url, **kwargs):
        if url.endswith(".xz"):
            raise OSError("no pack")
        return (remote / url.split("/patches/16.20/", 1)[1]).read_bytes()

    monkeypatch.setattr(public.http, "fetch", fetch)
    with pytest.raises(public.DatasetError):
        public.download("https://example.invalid", manifest, dest)
    assert (dest / "latest.json").read_bytes() == previous
    assert not dest.with_name("public.new").exists()
    store = public.PublicStore(dest.parent, bundled=tmp_path / "missing", base_url=[], managed_official=True)
    assert store.dataset.origin == "downloaded" and store.win_data("17")["sample_games"] == 400000


def test_existing_bad_download_falls_back_after_restart(tmp_path):
    bundled, downloaded = tmp_path / "bundled", tmp_path / "data" / "public"
    make_dataset(bundled)
    make_dataset(downloaded, snapshot_at="2026-10-28T00:00:00+08:00")
    rewrite(downloaded, "heroes.json", [1])
    for _ in range(2):
        store = public.PublicStore(downloaded.parent, bundled=bundled, base_url=[], managed_official=True)
        assert store.dataset.origin == "bundled" and store.win_data("17")


def test_bad_pending_never_replaces_current_download(tmp_path):
    root = tmp_path / "data"
    make_dataset(root / "public")
    pending = root / "public-pending"
    make_dataset(pending, snapshot_at="2026-10-28T00:00:00+08:00")
    rewrite(pending, "hero_augments.json", [1])
    store = public.PublicStore(root, bundled=tmp_path / "missing", base_url=[], managed_official=True)
    before = store.dataset.revision
    assert not store._activate_pending_data()
    assert store.dataset.revision == before and not pending.exists()
    assert public.Dataset(root / "public", "downloaded").revision == before


def test_bad_event_falls_back_to_normal_dataset(tmp_path):
    root = tmp_path / "data"
    make_dataset(root / "public")
    make_dataset(root / "public-event", snapshot_at="2026-10-28T00:00:00+08:00")
    rewrite(root / "public-event", "heroes.json", [1])
    store = public.PublicStore(root, bundled=tmp_path / "missing", base_url=[], managed_official=True)
    store.event = {"mode": "first-prismatic"}
    store.reload_dataset()
    assert store.event_fallback and store.dataset.mode == "normal"


def test_all_invalid_datasets_leave_official_fallback_available(tmp_path):
    make_dataset(tmp_path / "bundled")
    rewrite(tmp_path / "bundled", "heroes.json", [1])
    store = public.PublicStore(tmp_path / "data", bundled=tmp_path / "bundled", base_url=[], managed_official=True)
    assert store.dataset is None and store.ranks == {} and store.win_data("17") is None


