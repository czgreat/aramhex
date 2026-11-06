"""The complete publication/client process on an ephemeral loopback HTTP server."""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from conftest import make_dataset

from lolhex.data.official import ManagedOfficial
from scripts import official_data as publisher


def test_mixed_build_package_keeps_order_and_updates_same_date_after_game(tmp_path):
    from lolhex.data.public import PublicStore
    from lolhex.panel import hero_panel
    from lolhex.ui import buildview

    public = publisher.initialize_publication(tmp_path / "public")
    bundled_static, bundled_heroes = _source(tmp_path, "2026-10-22", "bundled", "提莫")
    static_path, heroes_path = _source(tmp_path, "2026-10-23", "source", "提莫")
    static = json.loads(static_path.read_text(encoding="utf-8"))
    static["items"] = {iid: {"name": name} for iid, name in
                       (("1001", "装备甲"), ("2001", "装备乙"), ("3001", "装备丙"))}
    static_path.write_text(json.dumps(static, ensure_ascii=False), encoding="utf-8")
    heroes = json.loads(heroes_path.read_text(encoding="utf-8"))
    hero = heroes["heroes"]["17"]
    hero["builds"]["core"] = [
        {"items": ["3001", "2001", "1001"], "ordered": True, "win": .4, "games": 1, "pick": .00001},
        {"items": ["1001", "3001", "2001"], "ordered": False, "win": .9, "games": 10000, "pick": .9},
        {"items": ["2001", "1001", "3001"], "ordered": False},
    ]
    hero["skills"] = [{"max": ["Q", "E", "W"], "win": .55}]
    heroes_path.write_text(json.dumps(heroes, ensure_ascii=False), encoding="utf-8")
    first = tmp_path / "candidate-1"
    _publish(first, publisher.create_candidate(static_path, heroes_path, first), public)

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(SimpleHTTPRequestHandler, directory=str(public)))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    busy = [False]
    try:
        client = ManagedOfficial(tmp_path / "official", bundled_static, bundled_heroes,
                                 minutes=lambda: 60, sources=lambda: [f"http://127.0.0.1:{server.server_port}"],
                                 hold=lambda: busy[0], allow_http_local=True)
        bundled = tmp_path / "stats-bundled"
        make_dataset(bundled)
        store = PublicStore(tmp_path / "store", bundled=bundled, base_url=[], tag_hosts=[],
                            event_source=lambda: None, check_updates=False)
        store.official = client
        assert client.sync(force=True)
        panel = hero_panel(store, "17", allow_network=False)
        text = "".join(buildview.lines(panel, None, True))
        assert text.index("装备丙 → 装备乙 → 装备甲") < text.index("装备甲 + 装备丙 + 装备乙")
        assert "装备乙 + 装备甲 + 装备丙" in text  # 没有胜率也显示。
        assert panel["skills"] == hero["skills"]
        assert "主 Q · 副 E" in buildview.skills_line(panel, True)

        # 只改最终推荐顺序：不伪造资料日期，通过新 revision 更新。
        hero["builds"]["core"].reverse()
        heroes_path.write_text(json.dumps(heroes, ensure_ascii=False), encoding="utf-8")
        second = tmp_path / "candidate-2"
        _publish(second, publisher.create_candidate(static_path, heroes_path, second), public)
        busy[0] = True
        assert not client.sync(force=True)
        assert client.status()["official_pending"] == 2
        assert hero_panel(store, "17", allow_network=False)["builds"]["core"][0]["ordered"] is True
        busy[0] = False
        assert client.activate_pending()
        updated = hero_panel(store, "17", allow_network=False)
        assert updated["official_revision"] == 2
        assert updated["date"] == panel["date"]
        assert [row["items"][0]["id"] for row in updated["builds"]["core"]] == ["2001", "1001", "3001"]
        assert updated["skills"] == panel["skills"]

        # 发布有效的空栏目可以撤下推荐，不从内置或上一份英雄资料补回。
        hero["builds"] = {"core": [], "shoes": [], "start": []}
        hero["skills"] = []
        heroes_path.write_text(json.dumps(heroes, ensure_ascii=False), encoding="utf-8")
        third = tmp_path / "candidate-3"
        _publish(third, publisher.create_candidate(static_path, heroes_path, third), public)
        assert client.sync(force=True)
        cleared = hero_panel(store, "17", allow_network=False)
        assert cleared["official_revision"] == 3
        assert cleared["date"] == panel["date"]
        assert all(cleared["builds"][key] == [] for key in ("core", "shoes", "start"))
        assert cleared["skills"] == []
        assert buildview.skills_line(cleared, True) is None
        assert not any(title in "".join(buildview.lines(cleared, None, True))
                       for title in ("核心三件", "鞋子", "出门装"))
    finally:
        server.shutdown()
        worker.join(5)
        server.server_close()


def _source(tmp_path, day, label, name):
    folder = tmp_path / label
    folder.mkdir()
    static = {"generated": day, "patch": "16.21",
              "heroes": {"17": {"id": "17", "name": name, "nick": "迅捷斥候", "alias": "Teemo"}},
              "augments": {"1": {"id": "1", "name": "测试", "rarity": "silver"}},
              "items": {"1001": {}}, "spells": {"4": {}}}
    heroes = {"generated": day, "patch": "16.21", "heroes": {"17": {
        "patch": "16.21", "date": day.replace("-", ""), "builds": {}, "augments": []}}}
    paths = folder / "static.json", folder / "heroes.json"
    for path, data in zip(paths, (static, heroes)):
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return paths


def _publish(candidate, meta, public):
    return publisher.publish_candidate(candidate, public, approved_sha256=meta["sha256"],
                                       approved_by="local simulation approval")


def test_candidate_approval_download_pending_corruption_and_recovery(tmp_path):
    public = publisher.initialize_publication(tmp_path / "public")
    bundled_static, bundled_heroes = _source(tmp_path, "2026-10-22", "bundled", "内置提莫")
    source = _source(tmp_path, "2026-10-23", "candidate-input", "新版提莫")
    candidate = tmp_path / "candidate"
    meta = publisher.create_candidate(*source, candidate)
    calls = []

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            super().do_GET()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(public)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    busy = [False]
    try:
        client = ManagedOfficial(tmp_path / "client", bundled_static, bundled_heroes,
                                 minutes=lambda: 60, sources=lambda: [f"http://127.0.0.1:{server.server_port}"],
                                 hold=lambda: busy[0], allow_http_local=True)
        assert client.snapshot.revision == 0
        assert client.sync(force=True) is False  # collection alone did not publish a manifest
        assert client.snapshot.static["heroes"]["17"]["name"] == "内置提莫"
        _publish(candidate, meta, public)
        busy[0] = True
        assert client.sync(force=True) is False
        assert client.status()["official_pending"] == 1
        assert client.snapshot.revision == 0
        busy[0] = False
        assert client.activate_pending() is True
        assert client.snapshot.static["heroes"]["17"]["name"] == "新版提莫"
        assert client.snapshot.revision == 1
        assert calls == ["/latest.json", "/latest.json", "/revisions/1/official.json.gz"]
        assert client.sync(force=True) is False  # unchanged version only checks the small manifest
        assert calls[-1] == "/latest.json"

        source2 = _source(tmp_path, "2026-10-24", "second-input", "第二版提莫")
        candidate2 = tmp_path / "second-candidate"
        meta2 = publisher.create_candidate(*source2, candidate2)
        release = _publish(candidate2, meta2, public)
        package = public / release["manifest"]["package"]["path"]
        valid_bytes = package.read_bytes()
        package.write_bytes(b"corrupted payload")  # simulate a broken upstream response, not an accepted package
        assert client.sync(force=True) is False
        assert client.snapshot.revision == 1
        assert client.snapshot.static["heroes"]["17"]["name"] == "新版提莫"
        assert client.last_error
        package.write_bytes(valid_bytes)
        assert client.sync(force=True) is True
        assert client.snapshot.revision == 2
        assert client.snapshot.static["heroes"]["17"]["name"] == "第二版提莫"
        assert not (public / ".publish.lock").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
