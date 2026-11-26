"""云控配置和查分客户端。"""
import json

import pytest

from lolhex import cloud, rank


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise cloud.requests.HTTPError(str(self.status_code))

    def json(self):
        if self.data is None:
            raise ValueError("not json")
        return self.data


@pytest.fixture
def cloud_json(fake_official):
    """官网上的云控配置换成这一份。"""
    def put(obj):
        fake_official.files[("config", "")] = json.dumps(obj).encode()
    return put


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    monkeypatch.setattr(cloud, "_cache", None)


def test_cloud_defaults_and_bad_values(cloud_json):
    assert cloud.get()["rank"]["enabled"] is True
    cloud_json({"rank": {"enabled": False, "message": "维护中", "evil": {"x": 1}}, "notice": "不是字典", "min_version": "1.2.0"})
    cfg = cloud.refresh()
    assert cfg["rank"]["enabled"] is False and "evil" not in cfg["rank"] and cfg["notice"] == {} and cfg["min_version"] == "1.2.0"


def test_event_requires_a_timezone_on_both_bounds(cloud_json):
    bounds = {"mode": "first-prismatic", "start": "2000-10-01T00:00:00+08:00", "end": "2099-10-01T00:00:00+08:00"}
    for key in ("start", "end"):
        cloud_json({"event": {**bounds, key: bounds[key].removesuffix("+08:00")}})
        cloud.refresh()
        assert cloud.event() is None
    cloud_json({"event": bounds})
    cloud.refresh()
    assert cloud.event()["mode"] == bounds["mode"]


def test_rank_lookup_paths(monkeypatch, fake_official):
    ok = {"as_of": "2026-10-16", "results": [{"region": "HN1", "region_name": "艾欧尼亚", "top": 0.031,
                                               "band": "high", "band_name": "高分", "games": 120}]}
    answers = {"a#1": (200, ok), "slow#1": (429, {"error": "查得太快了，请一分钟后再试"}), "down#1": (502, None),
               "x#1": (404, {"error": "没有查到"}), "bad#1": (200, ["不是字典"])}
    monkeypatch.setattr(fake_official, "rank", lambda rid, sites, timeout: answers[rid])
    res = rank.lookup("a#1")
    assert rank.describe(res).startswith("前 3.1%（高分）· 艾欧尼亚") and "截至 10-16" in rank.describe(res)
    for rid, msg in (("slow#1", "一分钟"), ("down#1", "暂时不可用"), ("x#1", "没有查到"), ("bad#1", "查询失败"),
                     ("没编号", "名字#编号")):
        with pytest.raises(rank.RankError, match=msg):
            rank.lookup(rid)
    monkeypatch.setattr(cloud, "_cache", cloud._merge({"rank": {"enabled": False, "message": "查询维护中"}}))
    with pytest.raises(rank.RankError, match="维护中"):
        rank.lookup("a#1")


def test_malformed_rank_result_is_rejected_on_fetch_and_restart(tmp_path, fake_official):
    from lolhex.config import Settings

    path = tmp_path / "config.json"
    valid = {"as_of": "2026-10-16", "results": [
        {"region_name": "艾欧尼亚", "top": 0.031, "band": "high", "band_name": "高分", "games": 120}]}
    for bad in ({**valid, "results": ["bad"]}, {**valid, "results": [{"band": "high"}]}, {**valid, "as_of": 20261016}):
        fake_official.rank_result = (200, bad)
        with pytest.raises(rank.RankError, match="查询失败"):
            rank.lookup("a#1")
        path.write_text(json.dumps({"my_rank": bad, "riot_id": "a#1"}), encoding="utf-8")
        s = Settings.load(path)
        assert s.my_rank == {} and s.riot_id == "a#1"

    path.write_text(json.dumps({"my_rank": {**valid, "queried_at": "invalid"}}), encoding="utf-8")
    assert Settings.load(path).my_rank == valid


def test_rank_id_from_client_copy_is_cleaned(fake_official):
    fake_official.rank_result = (404, {"error": "没有查到"})
    for raw in ("⁦魅 惑⁩#⁦60051⁩", " 魅 惑 ＃ 60051 ", "﻿魅 惑#60051​"):
        with pytest.raises(rank.RankError):
            rank.lookup(raw)
    assert [c[0] for c in fake_official.rank_calls] == ["魅 惑#60051"] * 3  # 名字中间的空格保留
    with pytest.raises(rank.RankError, match="名字#编号"):
        rank.lookup("⁦提莫⁩#⁦⁩")


def test_rank_unreachable(fake_official):
    fake_official.rank_result = rank.requests.ConnectionError("x")
    with pytest.raises(rank.RankError, match="连不上"):
        rank.lookup("a#1")


def test_cloud_mirrors_go_first(cloud_json):
    from lolhex import patch
    cloud_json({"mirrors": {
        "update": ["https://new.example/u/{version}/", "http://insecure.example/x"], "data": ["https://new.example/u/data"]},
        "features": {"autoshot": False}})
    cloud.refresh()
    src = patch.sources_for("1.2.3")
    assert src[0] == "https://new.example/u/1.2.3" and not any("insecure" in s for s in src)
    assert cloud.mirrors("data") == ["https://new.example/u/data"]
    assert cloud.feature("autoshot") is False and cloud.feature("other") is True


def test_community_list_is_sanitized(cloud_json):
    cloud_json({"community": [
        {"name": "QQ 群", "text": "123456", "url": "https://qm.qq.com/q/x", "qr": "https://site.example/img/qq.png", "primary": True},
        {"name": "B 站", "url": "javascript:alert(1)"},
        {"text": "没有名字"}, "不是字典",
        {"name": "赞助", "url": "https://afdian.com/a/x", "qr": "http://insecure/qr.png"},
    ]})
    cloud.refresh()
    c = cloud.community()
    assert [x["name"] for x in c] == ["QQ 群", "B 站", "赞助"]
    assert c[1]["url"] == "" and c[2]["qr"] == ""
    assert cloud.primary_link()["url"] == "https://qm.qq.com/q/x"


def test_install_id_is_never_sent_with_credentials(monkeypatch, tmp_path, fake_official, cloud_json):
    # 匿名编号：第一次打开时生成并存下来；只跟公开的更新检查一起发，要凭证的资源（官方模块发）不带
    from lolhex.config import Settings
    s = Settings.load(tmp_path / "config.json")
    assert len(s.install_id) == 16 and Settings.load(tmp_path / "config.json").install_id == s.install_id
    monkeypatch.setattr(cloud, "_install_id", None)
    latest = "https://site.example/u/latest.json"
    cloud.set_install_id(s.install_id)
    assert cloud.update_headers(latest)["X-AramHex-Id"] == s.install_id
    cloud_json({})
    cloud.refresh()
    assert fake_official.calls and s.install_id not in repr(fake_official.calls)
    cloud.set_install_id(None)
    h = cloud.update_headers(latest)
    assert "X-AramHex-Id" not in h and h["User-Agent"].startswith("aramhex/")


def test_update_policy_from_cloud(cloud_json):
    cloud_json({"update": {
        "check_minutes": 5, "data_minutes": "720", "rollout_version": "v1.2.0", "rollout_percent": 30}})
    cloud.refresh()
    assert cloud.check_minutes() == 30 and cloud.data_minutes() == 720       # 太勤的按下限
    ids = [f"id{i}" for i in range(400)]
    got = [i for i in ids if cloud.offer_update("1.2.0", i)]
    assert 60 < len(got) < 180                                              # 约 30% 的电脑先收到
    assert all(cloud.offer_update("1.2.0", i) == (cloud.bucket(i) < 30) for i in ids[:50])  # 同一台分组固定
    assert cloud.offer_update("1.3.0", "id1") and not cloud.offer_update("1.2.0", None)     # 别的版本不限；没有编号排最后
    cloud_json({"update": {"paused": True}})
    cloud.refresh()
    assert not cloud.offer_update("1.3.0", "id1") and cloud.check_minutes() == 180


def test_bad_cloud_values_never_crash(monkeypatch, tmp_path):
    # 云控里写错一个数（无穷大、超大、NaN、文字）、整个文件不是对象、存下的缓存坏了：都按默认值，不能让程序起不来
    bad = [{"update": {"check_minutes": 1e400, "data_minutes": "inf", "rollout_version": "1.2.0",
                       "rollout_percent": "nan", "paused": "false"}},
           {"update": {"check_minutes": 10 ** 400, "data_minutes": None, "rollout_percent": True}},
           [1, 2], "x", {"notice": {"id": 5, "text": 123}, "rank": {"enabled": "false"}, "features": {"a": "no"}}]
    for raw in bad:
        monkeypatch.setattr(cloud, "_cache", cloud._merge(raw))
        assert cloud.check_minutes() == 180 and cloud.data_minutes() == 360
        assert cloud.offer_update("1.2.0", "abc") is True                    # 文字 "false" 不算暂停，比例认不出按 100
    assert cloud.get()["notice"] == {"id": "5", "text": "123"} and cloud.get()["rank"]["enabled"] is True
    assert cloud.feature("a") is True
    monkeypatch.setattr(cloud, "_cache", None)
    cloud._path().parent.mkdir(parents=True, exist_ok=True)
    cloud._path().write_text("[1, 2, 3]", encoding="utf-8")                  # 存下的云控坏了
    assert cloud.check_minutes() == 180


def test_headers_version_everywhere_id_only_to_own_site(monkeypatch):
    # 1.1.1（A6）：下载数据、查新版本都带版本号；匿名编号只跟检查、下载更新发给本站，不发给 GitHub / jsDelivr
    from conftest import SITE as WEBSITE
    from lolhex import __version__
    monkeypatch.setattr(cloud, "_install_id", "abcd1234abcd1234")
    own = cloud.update_headers(f"{WEBSITE}/u/latest.json")
    assert own["User-Agent"] == f"aramhex/{__version__}" and own["X-AramHex-Id"] == "abcd1234abcd1234"
    for other in ("https://raw.githubusercontent.com/x/latest.json", "https://cdn.jsdelivr.net/gh/x", "",
                  "https://site.example.evil.example/u/"):
        h = cloud.update_headers(other)
        assert h["User-Agent"] == f"aramhex/{__version__}" and "X-AramHex-Id" not in h
    assert cloud.update_headers(f"{WEBSITE}/u/x", Range="bytes=0-9")["Range"] == "bytes=0-9"
    assert cloud.headers() == {"User-Agent": f"aramhex/{__version__}"}
    monkeypatch.setattr(cloud, "_install_id", None)
    assert "X-AramHex-Id" not in cloud.update_headers(f"{WEBSITE}/u/latest.json")


@pytest.mark.site
@pytest.mark.parametrize("usage", ["abcd1234abcd1234", None])
def test_id_goes_out_with_updates_only(monkeypatch, tmp_path, usage):
    # 看真正发出去的请求：官网上的资料镜像（统计、活动、官方资料、云控）也不带编号；关了使用统计都不带
    import requests
    from conftest import SITE as WEBSITE
    from lolhex import __version__, patch, update
    from lolhex.data import official as official_data
    from lolhex.data import public
    sent = []

    def send(self, request, **kw):
        sent.append((request.url, dict(request.headers)))
        raise requests.ConnectionError("测试：不联网")
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    monkeypatch.setattr(cloud, "_install_id", usage)

    def run(f, *a, **k):
        sent.clear()
        try:
            f(*a, **k)
        except (requests.RequestException, patch.NeedFullInstall):
            pass
        assert sent and all(u.startswith(WEBSITE + "/") and h["User-Agent"] == f"aramhex/{__version__}" for u, h in sent)
        return {h.get("X-AramHex-Id") for _, h in sent}
    base = f"{WEBSITE}/u/data"
    store = public.PublicStore(tmp_path / "data", bundled=tmp_path / "none", base_url=base,
                               tag_hosts=["site.example"], tag_fmt="https://{host}/u/data-v{seq}")
    store.event = {"mode": "first-prismatic", "label": "x"}
    reader = official_data.ManagedOfficial.__new__(official_data.ManagedOfficial)
    reader.session = requests.Session()
    for f, *a in ((store._sync_from, base), (store._look_for_offer,), (store._sync_event, base), (store._probe_tag,),
                  (public._fetch_file, f"{base}/a.json", 5, None), (reader._get, f"{base}/manifest.json", 100),
                  (cloud.refresh, 5, f"{WEBSITE}/config.json")):
        assert run(f, *a) == {None}, f
    up = f"{WEBSITE}/u/1.3.1"
    rel = "_internal/lolhex/a.py"
    manifest = {"version": "1.3.1", "files": {rel: {"size": 1, "sha256": "0" * 64}}, "patchable": [rel]}
    monkeypatch.setattr(patch, "check_manifest", lambda m, v: m)
    for f, *a in ((update.check_site, "1.3.0", 5, f"{WEBSITE}/u/latest.json"), (patch.fetch_manifest, "1.3.1", [up]),
                  (patch.rank_by_speed, [up], rel), (patch.download, manifest, [rel], tmp_path / "root", [up]),
                  (update.download, {"url": f"{up}/setup.exe", "file": "s.exe", "sha256": "0" * 64}, None, tmp_path / "dl")):
        assert run(f, *a) == {usage}, f


@pytest.mark.site
@pytest.mark.parametrize("usage", [None, "abcd1234abcd1234"])
@pytest.mark.parametrize("destination", ["/u/moved.json", "https://cdn.example/release.json",
                                         "https://site.example:444/release.json", "http://site.example/release.json"])
def test_update_redirect_scopes_install_id(monkeypatch, usage, destination):
    import requests
    from conftest import SITE
    from lolhex import update

    original = f"{SITE}/u/latest.json"
    sent = []

    def send(self, request, **kwargs):
        sent.append((request.url, request.headers.get("X-AramHex-Id")))
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 302 if request.url == original else 200
        if response.status_code == 302:
            response.headers["Location"] = destination
        response._content = b'{"version":"1.3.0"}'
        response._content_consumed = True
        return response

    monkeypatch.setattr(cloud, "_install_id", usage)
    monkeypatch.setattr(cloud, "sites", lambda: [SITE])
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    assert update.check_site("1.3.0", 5, original) is None
    if destination.startswith("/"):
        assert sent == [(original, usage), (SITE + destination, usage)]
    else:
        assert sent == [(original, usage), (destination, None)]


def test_build_data_download_uses_build_user_agent(monkeypatch):
    import requests
    from conftest import SITE
    from scripts import update_data

    def get(url, *, headers, timeout):
        assert url == f"{SITE}/latest.json"
        assert headers == {"User-Agent": "aramhex-build"}
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        return response

    monkeypatch.setattr(requests, "get", get)
    assert update_data._get(f"{SITE}/latest.json") == b"{}"
