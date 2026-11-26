"""云控换官网地址（site.urls，1.1.3）：新地址在前，官方模块内置的永远兜底。
1.3.0 起要凭证的资源由官方模块按 sites() 的先后去连，公开端只传地址列表。"""
import json

import pytest
import requests

from conftest import BACKUP_SITE as CN, SITE as WEBSITE
from lolhex import cloud, patch, rank, update

NEW = "https://new.example.cn"


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise cloud.requests.HTTPError(str(self.status_code))

    def json(self):
        return self.data


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    monkeypatch.setattr(cloud, "_cache", None)
    return tmp_path


def use(monkeypatch, raw):
    monkeypatch.setattr(cloud, "_cache", cloud._merge(raw))


def test_default_is_builtin_site():
    assert cloud.sites() == [WEBSITE, CN] and cloud.site() == WEBSITE
    assert "api" not in cloud.get()["rank"] and "url" not in cloud.get()["logs"]  # 地址只由官方模块给


def test_site_urls_are_validated():
    bad = ["http://x.cn", "https://x.cn/path", "https://localhost", "ftp://x.cn", 3, None, WEBSITE]
    assert cloud._merge({"site": {"urls": bad}})["site"]["urls"] == []
    assert cloud._merge({"site": {"urls": "https://x.cn"}})["site"]["urls"] == []      # 不是列表
    many = ["https://A.cn/", "https://a.cn", "https://b.cn", "https://c.cn:8443", "https://d.cn"]
    assert cloud._merge({"site": {"urls": many}})["site"]["urls"] == ["https://a.cn", "https://b.cn", "https://c.cn:8443"]


def test_resources_and_rank_get_the_site_list(monkeypatch, fake_official):
    use(monkeypatch, {"site": {"urls": [NEW]}})
    assert cloud.sites() == [NEW, WEBSITE, CN] and cloud.site() == NEW
    cloud.fetch("official://data/latest.json")
    assert fake_official.calls[-1][:3] == ("data", "latest.json", [NEW, WEBSITE, CN])
    fake_official.rank_result = (404, {"error": "没有查到"})
    with pytest.raises(rank.RankError):
        rank.lookup("a#1")
    assert fake_official.rank_calls[-1][1] == [NEW, WEBSITE, CN]
    # 云控里写的日志地址不收
    assert "url" not in cloud._merge({"logs": {"url": "https://evil.example/api/logs"}})["logs"]
    assert cloud.own(f"{NEW}/u/data") and cloud.own(f"{WEBSITE}/config.json") and not cloud.own("https://github.com/x")
    use(monkeypatch, {"site": {"urls": [CN]}})                  # 云控写内置的备用站：它排第一
    cloud.fetch("official://config")
    assert fake_official.calls[-1][:3] == ("config", "", [CN, WEBSITE])


def test_refresh_takes_config_and_keeps_site_order(monkeypatch, home, fake_official):
    (home / "cloud.json").write_text(json.dumps({"site": {"urls": [CN]}}), encoding="utf-8")
    fake_official.files[("config", "")] = json.dumps({"notice": {"id": "n1", "text": "新配置"}}).encode()
    cfg = cloud.refresh()
    assert fake_official.calls[0][2] == [CN, WEBSITE] and cfg["notice"]["text"] == "新配置"
    assert cloud.sites() == [WEBSITE, CN]              # 新配置里没写 site：下次回到内置
    assert json.loads((home / "cloud.json").read_text(encoding="utf-8"))["notice"]["id"] == "n1"


@pytest.mark.parametrize("down", [True, requests.ConnectionError("本机没准备好"), ValueError("资源名不对")])
def test_all_sites_down_keeps_last_config(monkeypatch, fake_official, down):
    use(monkeypatch, {"site": {"urls": [NEW]}, "notice": {"id": "k", "text": "上次"}})
    fake_official.down = down
    assert cloud.refresh()["notice"]["text"] == "上次"


@pytest.mark.parametrize("raw", [b"", b"<html>", b"[1]", "{\"a\": \"乱码".encode("gbk")])
def test_broken_config_keeps_last(monkeypatch, fake_official, raw):
    use(monkeypatch, {"notice": {"id": "k", "text": "上次"}})
    fake_official.files[("config", "")] = raw
    assert cloud.refresh()["notice"]["text"] == "上次"


@pytest.mark.site
def test_update_patch_and_links_use_new_site_first(monkeypatch):
    use(monkeypatch, {"site": {"urls": [NEW]}})
    src = patch.sources_for("1.1.3")
    assert src[:2] == [f"{NEW}/u/1.1.3", f"{WEBSITE}/u/1.1.3"]
    assert update.download_page() == f"{NEW}/#download"
    asked = []

    def get(url, **k):
        asked.append(url)
        if url.startswith(NEW):
            raise update.requests.ConnectionError("down")
        return Resp({"version": "1.0.0"})
    monkeypatch.setattr(update.requests, "get", get)
    assert update.check("1.0.0") is None
    assert asked[:2] == [f"{NEW}/u/latest.json", f"{WEBSITE}/u/latest.json"]


def test_slow_cache_write_does_not_block_get(monkeypatch, home):
    # 写缓存文件慢（杀毒软件扫描等）时，界面线程读配置不用等
    import threading
    writing, release = threading.Event(), threading.Event()

    def slow_write(path, text):
        writing.set()
        assert release.wait(5)
        path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(cloud, "atomic_write_text", slow_write)
    monkeypatch.setattr(cloud, "fetch", lambda url, timeout: Resp({"notice": {"id": "n2", "text": "新"}}))
    worker = threading.Thread(target=cloud.refresh, kwargs={"url": f"{WEBSITE}/config.json"})
    worker.start()
    try:
        assert writing.wait(5)
        got = {}
        reader = threading.Thread(target=lambda: got.update(cfg=cloud.get()))
        reader.start()
        reader.join(1)
        assert not reader.is_alive() and got["cfg"]["notice"]["text"] == "新"
    finally:
        release.set()
        worker.join(5)
    assert json.loads((home / "cloud.json").read_text(encoding="utf-8"))["notice"]["id"] == "n2"


def test_older_refresh_does_not_overwrite_newer_cache(monkeypatch, home):
    # 两次 refresh 同时在写：文件里留的是后一次的配置，和内存里一致
    import threading
    first_writing, release = threading.Event(), threading.Event()
    writes = []

    def write(path, text):
        writes.append(json.loads(text)["notice"]["id"])
        if len(writes) == 1:
            first_writing.set()
            assert release.wait(5)
        path.write_text(text, encoding="utf-8")

    replies = iter([{"notice": {"id": "old", "text": "旧"}}, {"notice": {"id": "new", "text": "新"}}])
    monkeypatch.setattr(cloud, "atomic_write_text", write)
    monkeypatch.setattr(cloud, "fetch", lambda url, timeout: Resp(next(replies)))
    url = f"{WEBSITE}/config.json"
    first = threading.Thread(target=cloud.refresh, kwargs={"url": url})
    first.start()
    assert first_writing.wait(5)
    second = threading.Thread(target=cloud.refresh, kwargs={"url": url})
    second.start()
    second.join(0.3)
    assert second.is_alive()          # 第二次等第一次写完再写
    release.set()
    first.join(5)
    second.join(5)
    assert writes == ["old", "new"] and cloud.get()["notice"]["id"] == "new"
    assert json.loads((home / "cloud.json").read_text(encoding="utf-8"))["notice"]["id"] == "new"
