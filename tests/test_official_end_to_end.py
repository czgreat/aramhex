"""The complete publication/client process on an ephemeral loopback HTTP server."""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from lolhex.data.official import ManagedOfficial
from scripts import official_data as publisher


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
