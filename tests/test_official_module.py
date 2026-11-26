"""官方模块（公开源码里没有）：有就用，没有时照常能用，并且一个官网请求都不发。"""
import json
import re
import sys
import types
from pathlib import Path

import pytest
import requests

import lolhex
from conftest import make_dataset
from lolhex import HOMEPAGE, RELEASES_PAGE, cloud, logupload, official, patch, rank, update
from lolhex.config import Settings
from lolhex.data import public
from lolhex.data.public import RAW_BASE, PublicStore

ROOT = Path(__file__).resolve().parent.parent


def test_public_source_has_no_server_address():
    bad = re.compile(r"aramhex\.(com|cn)")
    for folder in ("lolhex", "packaging", "scripts"):
        for p in (ROOT / folder).rglob("*"):
            if p.suffix in (".py", ".ps1", ".iss", ".spec", ".pem"):
                assert not bad.search(p.read_text(encoding="utf-8", errors="replace")), p


@pytest.fixture
def outbound(monkeypatch):
    sent = []

    def send(self, request, **kw):
        sent.append(request.url)
        raise requests.ConnectionError("测试：不联网")
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return sent


@pytest.mark.source
def test_source_run_only_talks_to_github(tmp_path, outbound):
    # 以前装过官方版：留下的云控（换过官网地址、点名本机）不能让自己打的包连官网
    cloud._path().parent.mkdir(parents=True, exist_ok=True)
    cloud._path().write_text(json.dumps({"site": {"urls": ["https://old.example"]}, "min_version": "9.9.9",
                                         "logs": {"request": ["0123456789abcdef"]}}), encoding="utf-8")
    assert not official.available() and cloud.sites() == []
    assert cloud.site() == HOMEPAGE and cloud.help_page() == HOMEPAGE
    assert cloud.refresh() == cloud._merge({}) and cloud.get()["min_version"] == ""
    s = Settings.load(tmp_path / "config.json")
    s.install_id = "0123456789abcdef"
    assert logupload.maybe_upload(s, {"logs": {"request": [s.install_id]}}) == []
    with pytest.raises(rank.RankError, match="不是官方安装包"):
        rank.lookup("a#1")
    assert update.check("1.0.0") is None and update.download_page() == RELEASES_PAGE
    with pytest.raises(update.NotOfficial):
        update.check("1.0.0", strict=True)
    with pytest.raises(patch.NeedFullInstall, match="不是官方安装包"):
        patch.prepare("9.9.9", root=tmp_path)
    store = PublicStore(tmp_path / "data", managed_official=True)
    assert store.base_urls == [RAW_BASE]
    store.refresh(force=True)
    assert outbound and all(u.startswith("https://raw.githubusercontent.com/") for u in outbound), outbound


def test_official_data_falls_back_to_github(tmp_path):
    assert PublicStore(tmp_path / "data").base_urls == ["official://data", RAW_BASE]


def test_data_is_synced_through_the_module(tmp_path, monkeypatch, fake_official, outbound):
    monkeypatch.setattr(official, "CHUNK", 700)  # 文件比一段大：分段取，拼起来按清单核对
    site = tmp_path / "site"
    manifest = make_dataset(site / "patches" / "16.21", snapshot_at="2099-01-01T00:00:00+08:00", patch="16.21")
    manifest["path"] = "patches/16.21"
    fake_official.files[("data", "latest.json")] = json.dumps(manifest).encode()
    for name in manifest["files"]:
        fake_official.files[("data", "patches/16.21/" + name)] = (site / "patches" / "16.21" / name).read_bytes()
    store = PublicStore(tmp_path / "data", bundled=tmp_path / "none")
    assert store.sync_data(force=True) and store.dataset.patch == "16.21"
    assert store.data_source == "official://data" and not outbound
    ranged = [c for c in fake_official.calls if c[5]]
    assert len(ranged) > len(manifest["files"]) and all(c[3] == "GET" and c[6] == official.CHUNK_TIMEOUT for c in ranged)


@pytest.mark.parametrize("down", [True, requests.ConnectionError("本机没准备好"), ValueError("资源名不对")])
def test_data_line_failure_goes_on_to_github(tmp_path, fake_official, outbound, down):
    fake_official.down = down
    store = PublicStore(tmp_path / "data", bundled=tmp_path / "none")
    assert store.sync_data(force=True) is False
    assert outbound and all(u.startswith(RAW_BASE) for u in outbound) and "官网：" in store.sync_error


def test_download_rejects_wrong_pieces(monkeypatch, fake_official):
    monkeypatch.setattr(official, "CHUNK", 4)
    fake_official.files[("data", "a.bin")] = b"0123456789"
    assert official.download("data", "a.bin", []) == b"0123456789"
    with pytest.raises(requests.HTTPError):
        official.download("data", "none.bin", [])
    with pytest.raises(requests.RequestException, match="上限"):
        official.download("data", "a.bin", [], maximum=9)
    good = fake_official.resource

    def reply(**change):
        def resource(kind, name, sites, **k):
            status, body, headers = good(kind, name, sites, **k)
            first = int(k["byte_range"][6:].split("-")[0])
            return change.get("status", status), body, dict(headers, **change.get("headers", lambda n: {})(first))
        return resource
    def whole(body, content_range):
        return lambda kind, name, sites, **k: (206, body, {"Content-Range": content_range})
    for bad in (reply(status=200), reply(headers=lambda n: {"Content-Range": "bytes 1-4/10"}),
                reply(headers=lambda n: {"Content-Range": ""}),
                reply(headers=lambda n: {"Content-Range": f"bytes {n}-{n + 3}/{11 if n else 10}"}),
                reply(headers=lambda n: {"Content-Range": f"bytes {n}-{n + 8}/10"}),
                reply(headers=lambda n: {"Content-Range": f"bytes {n}-{n + 3}/2"}),       # 终点超过总长
                whole(b"0123456789", "bytes 0-9/10"),                                     # 超过请求的终点
                whole(b"", "bytes 0-0/10"), whole(b"", "bytes 1-0/10")):                  # 空的一段不能一直要下去
        monkeypatch.setattr(fake_official, "resource", bad)
        with pytest.raises(requests.RequestException):
            official.download("data", "a.bin", [])
    monkeypatch.setattr(fake_official, "resource", reply(headers=lambda n: {"Content-Range": "bytes 0-3/2"}))
    with pytest.raises(requests.RequestException):
        official.download("data", "a.bin", [], maximum=2)  # 报的总长没超上限，实际给多了


def test_stats_file_bigger_than_manifest_stops_at_first_piece(tmp_path, monkeypatch, fake_official):
    monkeypatch.setattr(official, "CHUNK", 64)
    site = tmp_path / "site"
    manifest = make_dataset(site / "patches" / "16.21", snapshot_at="2099-01-01T00:00:00+08:00", patch="16.21")
    manifest["path"] = "patches/16.21"
    for name in manifest["files"]:
        fake_official.files[("data", "patches/16.21/" + name)] = (site / "patches" / "16.21" / name).read_bytes()
    first = next(iter(manifest["files"]))
    fake_official.files[("data", "patches/16.21/" + first)] += b" " * 100_000
    with pytest.raises(requests.RequestException, match="上限"):
        public.download("official://data", manifest, tmp_path / "out")
    assert [c[1] for c in fake_official.calls].count("patches/16.21/" + first) == 1


def test_resource_errors_are_ordinary_request_errors(monkeypatch, fake_official):
    fake_official.files[("config", "")] = "{\"t\": \"中文\"}".encode()
    r = official.resource("config", "", [])
    assert r.status_code == 200 and r.json() == {"t": "中文"} and r.text == "{\"t\": \"中文\"}"
    with pytest.raises(requests.HTTPError):
        official.resource("data", "x", []).raise_for_status()
    for e in (ValueError("资源名不对"), UnicodeEncodeError("ascii", "中", 0, 1, "x"), requests.ConnectionError("本机没准备好")):
        fake_official.down = e
        with pytest.raises(requests.RequestException):
            official.resource("data", "x", [])
    monkeypatch.setattr(official, "_impl", None)
    for call in (lambda: official.resource("data", "x", []), lambda: official.rank("a#1", [], 5)):
        with pytest.raises(requests.ConnectionError):
            call()
    assert official.sources("data") == []


def test_no_credential_ever_reaches_plain_requests(fake_official):
    for url in ("https://site.example/u/latest.json", RAW_BASE + "/latest.json", "https://site.example.evil.example/x", ""):
        assert "Authorization" not in cloud.update_headers(url)
    # 官网以外的地址冒充官方模块的地址：不会经官方模块发
    assert not official.is_resource("https://official//data") and official.split("official://data/a/b.json") == ("data", "a/b.json")


@pytest.mark.source
def test_broken_official_package_says_reinstall_but_still_updates(monkeypatch):
    monkeypatch.setattr(official, "expected", lambda: True)
    assert official.broken() and official.updatable()
    with pytest.raises(rank.RankError, match="没装完整"):
        rank.lookup("a#1")
    assert patch.sources_for("1.3.1")[0].startswith("https://raw.githubusercontent.com/")


def test_private_module_is_ignored_without_official_build_marker(monkeypatch):
    import sys

    m = types.ModuleType("lolhex._official")
    m.API = official.API
    monkeypatch.setitem(sys.modules, "lolhex._official", m)
    monkeypatch.setattr(official, "expected", lambda: False)
    assert official._load() is None


def test_source_ignores_marker_left_from_an_official_build(monkeypatch):
    marker = types.ModuleType("lolhex._build")
    marker.OFFICIAL = True
    module = types.ModuleType("lolhex._official")
    module.API = official.API
    monkeypatch.setitem(sys.modules, "lolhex._build", marker)
    monkeypatch.setitem(sys.modules, "lolhex._official", module)
    monkeypatch.setattr(lolhex, "_official", module, raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert not official.expected() and official._load() is None

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert official.expected() and official._load() is module


def test_wrong_api_module_is_refused(monkeypatch):
    monkeypatch.setattr(official, "expected", lambda: True)
    m = types.ModuleType("lolhex._official")
    m.API = official.API + 1
    monkeypatch.setitem(sys.modules, "lolhex._official", m)
    monkeypatch.setattr(lolhex, "_official", m, raising=False)
    assert official._load() is None
    m.API = official.API
    assert official._load() is m
