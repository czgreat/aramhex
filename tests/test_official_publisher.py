"""Offline publisher and a loopback-only candidate -> approval -> client replay."""
import json

import pytest

from scripts import official_data as publisher


def payload(day="2026-10-22", patch="16.21", name="提莫"):
    return {
        "static": {"generated": day, "patch": patch,
                   "heroes": {"17": {"id": "17", "name": name, "nick": "迅捷斥候", "alias": "Teemo"}},
                   "augments": {"1": {"id": "1", "name": "测试", "rarity": "silver"}},
                   "items": {"1001": {}}, "spells": {"4": {}}},
        "heroes": {"generated": day, "patch": patch, "heroes": {
            "17": {"patch": patch, "date": day.replace("-", ""), "builds": {}, "augments": []}}},
    }


def files(tmp_path, data=None, label="input"):
    data = data or payload()
    folder = tmp_path / label
    folder.mkdir()
    paths = folder / "static.json", folder / "heroes.json"
    for path, key in zip(paths, ("static", "heroes")):
        path.write_text(json.dumps(data[key], ensure_ascii=False), encoding="utf-8")
    return paths


def candidate(tmp_path, data=None, label="candidate"):
    static, heroes = files(tmp_path, data, label + "-input")
    directory = tmp_path / label
    return directory, publisher.create_candidate(static, heroes, directory)


def approve(directory, meta, publication):
    return publisher.publish_candidate(directory, publication, approved_sha256=meta["sha256"],
                                       approved_by="local simulation approval")


def test_candidate_is_isolated_and_deterministic(tmp_path):
    public = publisher.initialize_publication(tmp_path / "public")
    a, ma = candidate(tmp_path, label="a")
    b, mb = candidate(tmp_path, label="b")
    assert ma["sha256"] == mb["sha256"]
    assert (a / "official.json.gz").read_bytes() == (b / "official.json.gz").read_bytes()
    assert not (public / "latest.json").exists()
    assert publisher.candidate_report(a)["published"] is False
    static, heroes = files(tmp_path, label="inside-input")
    with pytest.raises(ValueError, match="inside a publication"):
        publisher.create_candidate(static, heroes, public / "bad-candidate")


def test_explicit_approval_hash_and_dedicated_publication_required(tmp_path):
    directory, meta = candidate(tmp_path)
    arbitrary = tmp_path / "unrelated"
    arbitrary.mkdir()
    (arbitrary / "important.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        publisher.initialize_publication(arbitrary)
    with pytest.raises(OSError):
        approve(directory, meta, arbitrary)
    public = publisher.initialize_publication(tmp_path / "public")
    with pytest.raises(ValueError, match="Approval does not match"):
        publisher.publish_candidate(directory, public, approved_sha256="0" * 64, approved_by="test")
    with pytest.raises(ValueError, match="approver"):
        publisher.publish_candidate(directory, public, approved_sha256=meta["sha256"], approved_by="")
    assert not (public / "latest.json").exists()
    assert (arbitrary / "important.txt").read_text(encoding="utf-8") == "keep"


def test_complete_hero_coverage_and_metadata_required(tmp_path):
    broken = payload()
    broken["static"]["heroes"]["1"] = {"name": "安妮"}
    static, heroes = files(tmp_path, broken)
    with pytest.raises(ValueError):
        publisher.create_candidate(static, heroes, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()
    directory, meta = candidate(tmp_path, label="good")
    (directory / "official.json.gz").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash/size"):
        publisher.read_candidate(directory)


def test_publish_unchanged_skips_new_revision_and_report_shows_diff(tmp_path):
    public = publisher.initialize_publication(tmp_path / "public")
    a, ma = candidate(tmp_path, label="a")
    assert approve(a, ma, public)["revision"] == 1
    original = (public / "latest.json").read_bytes()
    b, mb = candidate(tmp_path, label="b")
    assert approve(b, mb, public) == {"published": False, "reason": "unchanged", "revision": 1}
    assert (public / "latest.json").read_bytes() == original
    c, mc = candidate(tmp_path, payload("2026-10-23", name="提莫新版"), "c")
    report = publisher.candidate_report(c, public)
    assert report["changed"]["heroes"]["modified"] == ["17"]
    assert report["unchanged"] is False
    assert approve(c, mc, public)["revision"] == 2
    assert (public / "revisions/1/official.json.gz").read_bytes() == (a / "official.json.gz").read_bytes()


@pytest.mark.parametrize("day,patch", [("2026-10-21", "16.21"), ("2026-10-24", "16.20")])
def test_older_source_or_patch_cannot_overwrite_latest(tmp_path, day, patch):
    public = publisher.initialize_publication(tmp_path / "public")
    a, ma = candidate(tmp_path, label="a")
    approve(a, ma, public)
    original = (public / "latest.json").read_bytes()
    b, mb = candidate(tmp_path, payload(day, patch), "b")
    with pytest.raises(ValueError, match="rollback"):
        approve(b, mb, public)
    assert (public / "latest.json").read_bytes() == original


def test_publish_failure_keeps_live_manifest_and_can_retry(tmp_path, monkeypatch):
    public = publisher.initialize_publication(tmp_path / "public")
    a, ma = candidate(tmp_path, label="a")
    approve(a, ma, public)
    original = (public / "latest.json").read_bytes()
    b, mb = candidate(tmp_path, payload("2026-10-23"), "b")
    atomic = publisher._atomic

    def fail(*args):
        raise OSError("simulated disk error before latest switch")

    monkeypatch.setattr(publisher, "_atomic", fail)
    with pytest.raises(OSError, match="simulated disk"):
        approve(b, mb, public)
    assert (public / "latest.json").read_bytes() == original
    assert not (public / ".publish.lock").exists()
    monkeypatch.setattr(publisher, "_atomic", atomic)
    assert approve(b, mb, public)["revision"] == 3  # interrupted immutable revision 2 is not overwritten


def test_concurrent_publish_lock_does_not_change_live_data(tmp_path):
    public = publisher.initialize_publication(tmp_path / "public")
    a, ma = candidate(tmp_path)
    (public / ".publish.lock").write_text("another publisher", encoding="utf-8")
    with pytest.raises(FileExistsError):
        approve(a, ma, public)
    assert not (public / "latest.json").exists()
    assert (public / ".publish.lock").read_text(encoding="utf-8") == "another publisher"


def test_collect_requires_explicit_fetch_flag():
    with pytest.raises(SystemExit) as error:
        publisher.main(["collect", "--output", "not-created"])
    assert error.value.code == 2


def test_manual_collection_rejects_valid_but_partial_tencent_hero_list(tmp_path, monkeypatch):
    from lolhex.data import tencent

    monkeypatch.setattr(tencent, "fetch_versions", lambda: [{"name": "16.21"}])
    monkeypatch.setattr(tencent, "fetch_hero_list", lambda: {"valid": "partial response"})
    monkeypatch.setattr(tencent, "parse_hero_list", lambda raw: {"17": {"id": "17", "name": "提莫"}})
    with pytest.raises(ValueError, match="incomplete.*baseline"):
        publisher.collect_candidate(tmp_path / "candidate")
    assert not (tmp_path / "candidate").exists()


def test_candidate_cannot_remove_a_previously_published_hero(tmp_path):
    public = publisher.initialize_publication(tmp_path / "public")
    first = payload()
    first["static"]["heroes"]["1"] = {"id": "1", "name": "安妮", "nick": "黑暗之女", "alias": "Annie"}
    first["heroes"]["heroes"]["1"] = dict(first["heroes"]["heroes"]["17"])
    a, ma = candidate(tmp_path, first, "a")
    approve(a, ma, public)
    original = (public / "latest.json").read_bytes()
    b, mb = candidate(tmp_path, payload("2026-10-23"), "b")
    with pytest.raises(ValueError, match="omits heroes"):
        approve(b, mb, public)
    assert (public / "latest.json").read_bytes() == original
