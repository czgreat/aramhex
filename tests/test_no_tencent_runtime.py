"""玩家端（托管官方资料）运行时一个腾讯请求都不发：只在测试里从 requests 最底层记下所有出站请求来查，
程序本身不加拦截（代码里本来就不请求腾讯）。以后有人误加了腾讯请求，这个测试会失败。"""

import pytest
import requests

from lolhex import cloud
from lolhex.data.icons import IconCache
from lolhex.data.public import PublicStore


@pytest.fixture
def outbound(monkeypatch):
    sent = []

    def send(self, request, **kw):
        sent.append(request.url)
        raise requests.ConnectionError("测试：不联网")
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    # 云控把官方资料源和检查都打开，走一遍真实的「检查更新」路径
    monkeypatch.setattr(cloud, "get", lambda: {**cloud.DEFAULT,
                                               "mirrors": {"update": [], "data": [], "official": ["https://aramhex.com/u/official"]},
                                               "update": {**cloud.DEFAULT["update"], "official_minutes": 60}})
    return sent


def test_managed_store_never_contacts_tencent(tmp_path, outbound):
    store = PublicStore(tmp_path / "data", managed_official=True)
    store.refresh(force=True)
    hid = next(iter(store.heroes))
    assert store.hero(hid, allow_network=True)            # 英雄资料来自内置
    assert store.official_hero(hid, allow_network=True)
    icons = IconCache(tmp_path / "img", allow_network=False)
    icons.local("item", "3031", "https://game.gtimg.cn/images/lol/act/img/item/3031.png")
    assert outbound, "应该至少检查过一次自有服务器"
    assert not [u for u in outbound if any(d in u for d in ("qq.com", "gtimg.c"))], outbound
    assert all(cloud.own(u) or "github" in u or "jsdelivr" in u for u in outbound), outbound  # 自己的官网（含备用 aramhex.cn）

