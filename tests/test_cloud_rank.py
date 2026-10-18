"""云控配置和查分客户端。"""
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


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    monkeypatch.setattr(cloud, "_cache", None)


def test_cloud_defaults_and_bad_values(monkeypatch):
    assert cloud.get()["rank"]["enabled"] is True
    monkeypatch.setattr(cloud.requests, "get", lambda url, **k: Resp({"rank": {"enabled": False, "message": "维护中", "evil": {"x": 1}},
                                                                      "notice": "不是字典", "min_version": "1.2.0"}))
    cfg = cloud.refresh()
    assert cfg["rank"]["enabled"] is False and "evil" not in cfg["rank"] and cfg["notice"] == {} and cfg["min_version"] == "1.2.0"


def test_rank_lookup_paths(monkeypatch):
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append(headers)
        rid = params["id"]
        if rid == "a#1":
            return Resp({"as_of": "2026-10-16", "results": [{"region": "HN1", "region_name": "艾欧尼亚", "top": 0.031,
                                                              "band": "high", "band_name": "高分", "games": 120}]})
        if rid == "slow#1":
            return Resp({"error": "查得太快了，请一分钟后再试"}, 429)
        if rid == "down#1":
            return Resp(None, 502)
        return Resp({"error": "没有查到"}, 404)
    monkeypatch.setattr(rank.requests, "get", get)
    res = rank.lookup("a#1")
    assert calls[0]["X-AramHex"] and rank.describe(res).startswith("前 3.1%（高分）· 艾欧尼亚") and "截至 10-16" in rank.describe(res)
    for rid, msg in (("slow#1", "一分钟"), ("down#1", "暂时不可用"), ("x#1", "没有查到"), ("没编号", "名字#编号")):
        with pytest.raises(rank.RankError, match=msg):
            rank.lookup(rid)
    cloud._cache = cloud._merge({"rank": {"enabled": False, "message": "查询维护中"}})
    with pytest.raises(rank.RankError, match="维护中"):
        rank.lookup("a#1")


def test_rank_unreachable(monkeypatch):
    def boom(*a, **k):
        raise rank.requests.ConnectionError("x")
    monkeypatch.setattr(rank.requests, "get", boom)
    with pytest.raises(rank.RankError, match="连不上"):
        rank.lookup("a#1")


def test_cloud_mirrors_go_first(monkeypatch):
    from lolhex import patch
    monkeypatch.setattr(cloud.requests, "get", lambda url, **k: Resp({"mirrors": {
        "update": ["https://new.example/u/{version}/", "http://insecure.example/x"], "data": ["https://new.example/u/data"]},
        "features": {"autoshot": False}}))
    cloud.refresh()
    src = patch.sources_for("1.2.3")
    assert src[0] == "https://new.example/u/1.2.3" and not any("insecure" in s for s in src)
    assert cloud.mirrors("data") == ["https://new.example/u/data"]
    assert cloud.feature("autoshot") is False and cloud.feature("other") is True


def test_community_list_is_sanitized(monkeypatch):
    monkeypatch.setattr(cloud.requests, "get", lambda url, **k: Resp({"community": [
        {"name": "QQ 群", "text": "123456", "url": "https://qm.qq.com/q/x", "qr": "https://aramhex.com/img/qq.png", "primary": True},
        {"name": "B 站", "url": "javascript:alert(1)"},
        {"text": "没有名字"}, "不是字典",
        {"name": "赞助", "url": "https://afdian.com/a/x", "qr": "http://insecure/qr.png"},
    ]}))
    cloud.refresh()
    c = cloud.community()
    assert [x["name"] for x in c] == ["QQ 群", "B 站", "赞助"]
    assert c[1]["url"] == "" and c[2]["qr"] == ""
    assert cloud.primary_link()["url"] == "https://qm.qq.com/q/x"


def test_install_id_sent_only_when_enabled(monkeypatch, tmp_path):
    # 匿名编号：第一次打开时生成并存下来；开着时查云控带上，关掉就不带
    from lolhex.config import Settings
    s = Settings.load(tmp_path / "config.json")
    assert len(s.install_id) == 16 and Settings.load(tmp_path / "config.json").install_id == s.install_id
    seen = []
    monkeypatch.setattr(cloud.requests, "get", lambda url, headers=None, **k: seen.append(dict(headers or {})) or Resp({}))
    monkeypatch.setattr(cloud, "_install_id", None)
    cloud.set_install_id(s.install_id)
    cloud.refresh()
    cloud.set_install_id(None)
    cloud.refresh()
    assert seen[0]["X-AramHex-Id"] == s.install_id and "X-AramHex-Id" not in seen[1]
    assert seen[1]["User-Agent"].startswith("aramhex/")
