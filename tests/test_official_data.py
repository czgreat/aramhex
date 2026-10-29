import copy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from lolhex.data.official import ManagedOfficial, OfficialDataError, check_manifest, check_payload


def payload(patch="16.21", day="2026-10-22"):
    return {"static": {"patch": patch, "generated": day,
                       "heroes": {"17": {"id": "17", "name": "提莫", "nick": "迅捷斥候", "alias": "Teemo"}},
                       "augments": {"1": {"id": "1", "name": "测试", "rarity": "silver"}},
                       "items": {"1001": {}}, "spells": {"4": {}}},
            "heroes": {"patch": patch, "generated": day,
                       "heroes": {"17": {"patch": patch, "date": day.replace("-", ""),
                                         "augments": [], "builds": {}, "splash": None}}}}


def package(data=None, revision=1):
    data = data or payload()
    decoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
    blob = gzip.compress(decoded, mtime=0)
    manifest = {"schema_version": 1, "revision": revision, "patch": data["static"]["patch"],
                "source_date": data["static"]["generated"], "generated_at": "2026-10-24T20:00:00+08:00",
                "min_client": "1.1.2", "package": {"path": f"revisions/{revision}/official.json.gz",
                "size": len(blob), "unpacked_size": len(decoded), "sha256": hashlib.sha256(blob).hexdigest()}}
    return manifest, blob


class Response:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def iter_content(self, size):
        yield self.data

    def close(self):
        pass


class Server:
    def __init__(self, data=None, revision=1):
        self.manifest, self.blob = package(data, revision)
        self.calls = []

    def get(self, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        self.calls.append(url)
        return Response(json.dumps(self.manifest).encode() if url.endswith("/latest.json") else self.blob)


@pytest.fixture
def setup(tmp_path):
    data = payload()
    static_path, heroes_path = tmp_path / "static.json", tmp_path / "heroes.json"
    static_path.write_text(json.dumps(data["static"]), encoding="utf-8")
    heroes_path.write_text(json.dumps(data["heroes"]), encoding="utf-8")
    server = Server()
    now = [1000000.0]
    config = {"minutes": 60, "sources": ["https://own.example/official"], "hold": False}

    def create():
        return ManagedOfficial(tmp_path / "official", static_path, heroes_path,
                               minutes=lambda: config["minutes"], sources=lambda: config["sources"],
                               hold=lambda: config["hold"], clock=lambda: now[0], session=server)
    return create, server, now, config


def test_constructor_uses_complete_bundled_without_network(setup):
    create, server, _, _ = setup
    client = create()
    assert server.calls == []
    assert client.snapshot.origin == "bundled"
    assert client.snapshot.heroes["17"]["builds"] == {}


def test_manifest_accepts_test_client_version(setup):
    manifest, _ = package(revision=2)
    assert check_manifest(manifest, "1.1.3-test.2")["revision"] == 2


def test_download_validate_activate_and_restart(setup):
    create, server, _, _ = setup
    client = create()
    assert client.sync()
    assert client.snapshot.revision == 1
    assert client.snapshot.origin == "downloaded"
    assert len(server.calls) == 2
    restarted = create()
    assert restarted.snapshot.revision == 1
    assert len(server.calls) == 2


@pytest.mark.parametrize("deferred", [False, True])
def test_activation_logs_version_and_origin_once(setup, caplog, deferred):
    create, _, _, config = setup
    client = create()
    config["hold"] = deferred
    with caplog.at_level("INFO", logger="lolhex.data.official"):
        assert client.sync() is (not deferred)
        if deferred:
            assert not caplog.records
            config["hold"] = False
            assert client.activate_pending()
        assert not client.activate_pending()
        assert not client.sync(force=True)
    records = [r for r in caplog.records if "官方资料已切换" in r.getMessage()]
    assert len(records) == 1 and records[0].levelname == "INFO"
    assert "版本=16.21 第1版 来源=downloaded" in records[0].getMessage()


def test_same_revision_only_checks_manifest_and_obeys_interval(setup):
    create, server, now, _ = setup
    client = create()
    assert client.sync()
    assert not client.sync()
    assert len(server.calls) == 2
    now[0] += 3600
    assert not client.sync()
    assert len(server.calls) == 3


def test_thirty_days_and_shortened_cloud_interval(setup):
    create, server, now, config = setup
    config["minutes"] = 43200
    client = create()
    assert client.sync()
    now[0] += 2 * 3600
    assert not client.sync()
    assert client.seconds_until_check() > 29 * 86400
    config["minutes"] = 60
    assert client.seconds_until_check() == 0
    assert not client.sync()
    assert len(server.calls) == 3


def test_thirty_day_check_schedule_survives_restart_and_shortened_interval(setup):
    create, server, now, config = setup
    config["minutes"] = 43200
    client = create()
    assert client.sync()
    now[0] += 2 * 3600
    restarted = create()
    assert not restarted.sync()
    assert len(server.calls) == 2
    assert restarted.seconds_until_check() > 29 * 86400
    config["minutes"] = 60
    assert not restarted.sync()
    assert len(server.calls) == 3


def test_clock_rollback_does_not_leave_schedule_stuck(setup):
    create, server, now, _ = setup
    client = create()
    assert client.sync()
    now[0] -= 24 * 3600
    restarted = create()
    assert restarted.seconds_until_check() == 0
    assert not restarted.sync()
    assert len(server.calls) == 3
    assert restarted.seconds_until_check() == 3600


def test_malformed_schedule_does_not_disable_bundled_data(setup):
    create, _, _, _ = setup
    client = create()
    client.root.mkdir(parents=True)
    (client.root / "check-state.json").write_text('{"last_check": "broken"}', encoding="utf-8")
    restarted = create()
    assert restarted.snapshot.origin == "bundled"
    assert restarted.seconds_until_check() == 0


def test_closed_or_missing_source_does_not_fetch_even_force(setup):
    create, server, _, config = setup
    config["minutes"] = 0
    client = create()
    assert not client.sync(force=True)
    config["minutes"] = 60
    config["sources"] = []
    assert not client.sync(force=True)
    assert server.calls == []


def test_reenable_cloud_fetches_and_source_change_checks_immediately(setup):
    create, server, _, config = setup
    config["minutes"] = 0
    client = create()
    assert not client.sync()
    config["minutes"] = 60
    assert client.sync()
    config["sources"] = ["https://another-own.example/official"]
    assert not client.sync()
    assert server.calls[-1].startswith(config["sources"][0])


def test_game_holds_complete_snapshot_until_safe_and_survives_restart(setup):
    create, server, _, config = setup
    config["hold"] = True
    client = create()
    assert not client.sync()
    assert client.snapshot.revision == 0
    assert client.status()["official_pending"] == 1
    restarted = create()
    assert restarted.snapshot.revision == 0
    config["minutes"] = 0
    config["hold"] = False
    assert restarted.sync()
    assert restarted.snapshot.revision == 1
    assert len(server.calls) == 2


@pytest.mark.parametrize("damage", ["hash", "size", "gzip", "unpacked", "missing_hero", "date"])
def test_invalid_download_keeps_previous_snapshot(setup, damage):
    create, server, now, _ = setup
    client = create()
    assert client.sync()
    before = client.snapshot
    server.manifest, server.blob = package(payload(day="2026-10-23"), 2)
    if damage == "hash":
        server.manifest["package"]["sha256"] = "0" * 64
    elif damage == "size":
        server.manifest["package"]["size"] += 1
    elif damage == "gzip":
        server.blob = b"not gzip"
        server.manifest["package"].update(size=len(server.blob), sha256=hashlib.sha256(server.blob).hexdigest())
    elif damage == "unpacked":
        server.manifest["package"]["unpacked_size"] -= 1
    elif damage == "missing_hero":
        data = payload(day="2026-10-23")
        data["heroes"]["heroes"].clear()
        server.manifest, server.blob = package(data, 2)
    elif damage == "date":
        server.manifest["source_date"] = "2026-10-24"
    now[0] += 3600
    assert not client.sync()
    assert client.snapshot is before
    assert client.last_error
    assert create().snapshot.revision == 1


def test_failed_check_uses_memory_backoff_without_persisting_check_time(setup):
    create, server, now, config = setup
    client = create()
    server.get = lambda url, **kwargs: (_ for _ in ()).throw(OSError("offline"))
    assert not client.sync(force=True)
    assert 0 < client.seconds_until_check() <= 600
    assert not (client.root / "check-state.json").exists()
    now[0] += 601
    assert not client.sync(force=True)


def test_incompatible_client_is_rejected_before_package_download(setup):
    create, server, _, _ = setup
    server.manifest["min_client"] = "99.0.0"
    client = create()
    assert not client.sync()
    assert len(server.calls) == 1
    assert client.snapshot.revision == 0


def test_bundle_upgrade_does_not_load_older_download(setup):
    create, _, _, _ = setup
    client = create()
    assert client.sync()
    for name, obj in (("static.json", payload(patch="16.22", day="2026-11-01")["static"]),
                      ("heroes.json", payload(patch="16.22", day="2026-11-01")["heroes"])):
        (client.root.parent / name).write_text(json.dumps(obj), encoding="utf-8")
    newer_install = create()
    assert newer_install.snapshot.revision == 0
    assert newer_install.snapshot.static["patch"] == "16.22"


@pytest.mark.parametrize("path", ["../x.json.gz", "https://x/x.json.gz", "/x.json.gz", "x\\x.json.gz", "%2e%2e/x.json.gz"])
def test_manifest_rejects_unsafe_paths(path):
    manifest, _ = package()
    manifest["package"]["path"] = path
    with pytest.raises(OfficialDataError):
        check_manifest(manifest)


def test_http_only_allowed_for_explicit_local_simulation(setup):
    create, server, _, config = setup
    config["sources"] = ["http://127.0.0.1:12345"]
    client = create()
    assert not client.sync()
    assert not server.calls
    client.allow_http_local = True
    assert client.sync()


@pytest.mark.parametrize("url", ["https://mlol.qt.qq.com/go", "https://game.gtimg.cn/images", "https://raw.communitydragon.org/latest"])
def test_third_party_source_cannot_enable_direct_client_fetch(setup, url):
    create, server, _, config = setup
    config["sources"] = [url]
    client = create()
    assert not client.sync()
    assert server.calls == []


def test_corrupt_local_active_falls_back_to_bundle(setup):
    create, _, _, _ = setup
    client = create()
    assert client.sync()
    next((client.root / "revisions").rglob("package.gz")).write_bytes(b"corrupt")
    restarted = create()
    assert restarted.snapshot.origin == "bundled"
    assert restarted.last_error


def test_validation_rejects_cross_version_payload():
    data = copy.deepcopy(payload())
    data["heroes"]["patch"] = "16.22"
    with pytest.raises(OfficialDataError):
        check_payload(data)


def rich_payload(day="2026-10-23"):
    data = payload(day=day)
    hero = data["heroes"]["heroes"]["17"]
    hero.update(augments=[{"id": "1", "pick": 0.1}],
                builds={"core": [{"items": ["1001"], "win": 0.5, "pick": 0.1}],
                        "first": [{"item": "1001", "win": 0.5, "games": 1000}]},
                skills=[{"max": ["Q", "E", "W"], "games": 1000, "win": 0.5,
                         "sequences": [{"seq": ["Q", "W"], "games": 10, "win": 0.5}]}],
                partners=[{"hero": "17", "win": 0.5, "pick": 0.1}],
                spells={"Q": {"name": "致盲吹箭", "icon": ""}})
    return data


BAD_SEMANTICS = [
    (("static", "augments", "1"), None),
    (("static", "items", "1001"), []),
    (("static", "spells", "4"), None),
    (("static", "heroes", "17", "name"), 17),
    (("static", "heroes", "17", "nick"), None),
    (("static", "heroes", "17", "alias"), []),
    (("static", "heroes", "17", "keywords"), []),
    (("static", "augments", "1", "id"), "different"),
    (("static", "augments", "1", "name"), True),
    (("static", "augments", "1", "rarity"), {}),
    (("static", "augments", "1", "modes"), ["KIWI", None]),
    (("heroes", "heroes", "17", "augments", 0), None),
    (("heroes", "heroes", "17", "augments", 0, "id"), []),
    (("heroes", "heroes", "17", "builds", "core", 0), None),
    (("heroes", "heroes", "17", "builds", "core", 0, "items"), None),
    (("heroes", "heroes", "17", "builds", "core", 0, "items"), [{}]),
    (("heroes", "heroes", "17", "builds", "core", 0, "pick"), "0.1"),
    (("heroes", "heroes", "17", "builds", "core", 0, "win"), float("inf")),
    (("heroes", "heroes", "17", "builds", "first", 0, "item"), {}),
    (("heroes", "heroes", "17", "builds", "first", 0, "games"), float("nan")),
    (("heroes", "heroes", "17", "builds", "first", 0, "games"), True),
    (("heroes", "heroes", "17", "skills"), {}),
    (("heroes", "heroes", "17", "skills", 0), None),
    (("heroes", "heroes", "17", "skills", 0, "max"), [{}]),
    (("heroes", "heroes", "17", "skills", 0, "sequences", 0, "seq"), "QWE"),
    (("heroes", "heroes", "17", "partners", 0, "hero"), []),
    (("heroes", "heroes", "17", "spells", "Q"), None),
    (("heroes", "heroes", "17", "spells", "Q", "name"), {}),
    (("heroes", "heroes", "17", "splash"), {}),
]


@pytest.mark.parametrize("path,value", BAD_SEMANTICS)
def test_valid_hash_but_invalid_semantics_keeps_previous_snapshot(setup, path, value):
    create, server, now, _ = setup
    client = create()
    assert client.sync()
    before = client.snapshot
    data = rich_payload()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(OfficialDataError):
        check_payload(data)
    # The manifest contains this malformed payload's correct lengths and SHA-256.
    server.manifest, server.blob = package(data, 2)
    now[0] += 3600
    assert not client.sync()
    assert client.snapshot is before
    assert client.last_error
    assert client.status()["official_pending"] is None
    assert create().snapshot.revision == 1


def test_real_bundled_payload_satisfies_runtime_semantic_schema():
    from lolhex.data.official import STATIC, HEROES

    data = {"static": json.loads(STATIC.read_text(encoding="utf-8")),
            "heroes": json.loads(HEROES.read_text(encoding="utf-8"))}
    assert check_payload(data) is data


def test_optional_absent_fields_and_unknown_extensions_remain_compatible():
    data = rich_payload()
    data["future_extension"] = {"anything": [None, 1]}
    data["static"]["augments"]["1"]["future_extension"] = [None]
    data["heroes"]["heroes"]["17"]["builds"]["core"][0]["win"] = None
    assert check_payload(data) is data


def with_added_hero(data):
    data = copy.deepcopy(data)
    data["static"]["heroes"]["18"] = {"id": "18", "name": "崔丝塔娜", "nick": "麦林炮手", "alias": "Tristana"}
    data["heroes"]["heroes"]["18"] = copy.deepcopy(data["heroes"]["heroes"]["17"])
    return data


def test_download_cannot_remove_hero_from_both_tables_compared_with_bundle(setup):
    create, _, _, _ = setup
    client = create()
    bigger = with_added_hero(payload())
    for name, obj in (("static.json", bigger["static"]), ("heroes.json", bigger["heroes"])):
        (client.root.parent / name).write_text(json.dumps(obj), encoding="utf-8")
    client = create()
    assert not client.sync()
    assert set(client.snapshot.heroes) == {"17", "18"}
    assert client.last_error


def test_add_hero_is_accepted_then_both_tables_cannot_drop_it(setup):
    create, server, now, _ = setup
    client = create()
    server.manifest, server.blob = package(with_added_hero(payload()), 1)
    assert client.sync()
    assert set(client.snapshot.heroes) == {"17", "18"}
    server.manifest, server.blob = package(payload(day="2026-10-23"), 2)
    now[0] += 3600
    assert not client.sync()
    assert client.snapshot.revision == 1
    assert set(client.snapshot.heroes) == {"17", "18"}


def test_new_revision_removes_old_revision_folder(setup, tmp_path):
    """换上第 2 版后，第 1 版的目录删掉，不会每次更新都多留一份。"""
    create, server, now, _ = setup
    client = create()
    assert client.sync()
    server.manifest, server.blob = package(revision=2)
    now[0] += 3600
    assert client.sync()
    assert client.snapshot.revision == 2
    folders = [d.name for d in (tmp_path / "official" / "revisions").iterdir()]
    assert len(folders) == 1 and folders[0].startswith("2-")
    assert create().snapshot.revision == 2  # 重启后照样读到第 2 版
