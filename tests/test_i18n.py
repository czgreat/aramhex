"""外服：各语言名字表、认字匹配、选英雄界面的字、韩服识别数据下载。"""
import hashlib
from types import SimpleNamespace

import numpy as np
import pytest

from lolhex import i18n, langpack
from lolhex.engine import augment_matcher
from lolhex.vision import champselect
from lolhex.vision.matcher import NameMatcher


def test_names_by_language():
    assert i18n.augment_names("zh_CN") == {} and i18n.hero_names("zh_CN") == {}  # 国服用腾讯的名字
    assert i18n.augment_names("en_US")["1305"] == "Leg Day"
    assert i18n.traditional_augment_names()["1305"] == "腿部訓練日"   # 繁中是另一套译名，不是简转繁
    assert i18n.normalize_lang("zh_TW") == "zh_CN" and i18n.augment_names("zh_TW") == {}  # 繁中并进了中文
    assert i18n.augment_names("ko_KR")["1305"] == "하체 운동의 날"
    assert i18n.hero_names("ja_JP")["157"][0] == "ヤスオ"
    assert i18n.ui_text("bench", "zh_CN") == "可用"
    assert i18n.ui_text("bench", "en_US") == "Available Champions"
    assert i18n.normalize_lang("xx") == "zh_CN"
    assert i18n.ui_texts("bench", "zh_CN") == ["可用", "可使用的英雄"] and i18n.ui_texts("bench", "en_US") == ["Available Champions"]


def test_augment_matcher_uses_game_language():
    store = SimpleNamespace(mayhem_augments=lambda: {"1305": {"name": "练腿日"}, "1195": {"name": "巨人杀手"}})
    cn = augment_matcher(store, SimpleNamespace(game_lang="zh_CN", match_threshold=72.0))
    assert cn.match("练腿日")[0] == "1305" and cn.match("Leg Day")[0] is None
    assert cn.match("腿部訓練日")[0] == "1305"      # 中文模式也认繁中（台服）的名字
    en = augment_matcher(store, SimpleNamespace(game_lang="en_US", match_threshold=72.0))
    assert en.match("Leg Day")[0] == "1305" and en.match("Giant Slayer")[0] == "1195"
    ko = augment_matcher(store, SimpleNamespace(game_lang="ko_KR", match_threshold=72.0))
    assert ko.match("하체 운동의 날")[0] == "1305"


def test_matcher_several_names_and_short_english():
    m = NameMatcher({"1": ["Stats!", "属性！"], "2": "From Beginning To End"})
    assert m.match("Stats!")[0] == "1" and m.match("属性")[0] == "1"
    assert m.match("Start")[0] is None              # 短英文名差一个字母不算（以前 80 分就过了）
    assert m.match("From Beginning To Erd")[0] == "2"


def test_traditional_lookalike_chars():
    tw = augment_matcher(SimpleNamespace(mayhem_augments=lambda: {"1384": {"name": "捐赠"}}),
                         SimpleNamespace(game_lang="zh_CN", match_threshold=72.0))
    assert tw.match("斗內")[0] == "1384" and tw.match("斗内")[0] == "1384"  # 单独认标题时「內」常读成「内」


def test_chinese_tries_traditional_ui_text_sometimes():
    from lolhex.engine import Engine
    eng = Engine.__new__(Engine)
    tried = []

    def fn(t):
        tried.append(t)
        return t == "可使用的英雄"
    texts = ["可用", "可使用的英雄"]
    for eng._cs_tick in (1, 2, 3):
        assert not eng._try_texts(texts, fn)
    assert tried == ["可用"] * 3                          # 繁中写法不是每次都试：国服在大厅几乎不多花时间
    eng._cs_tick = 4
    assert eng._try_texts(texts, fn) and tried[-2:] == ["可用", "可使用的英雄"]
    tried.clear()
    eng._cs_tick = 5
    assert eng._try_texts(texts, fn) and tried == ["可使用的英雄"]   # 认到过繁中以后先试繁中
    tried.clear()
    eng._cs_tick = 6
    assert eng._try_texts(texts, lambda t: tried.append(t) or t == "可用") and tried == ["可使用的英雄", "可用"]  # 简体每次都试
    tried.clear()
    assert eng._try_texts(texts, fn) is False and tried == ["可用"]            # 又改回先试简体


def test_marker_text_long_and_partial():
    ok = champselect._has_text("Available Champions")
    assert ok(("Available Champions", 0.9))
    assert ok(("Avai1able Champions", 0.9))          # 认错一个字母
    assert not ok(("Play Again", 0.9))
    cn = champselect._has_text("可用")
    assert cn(("可 用", 0.9)) and not cn(("可用", 0.3)) and not cn(("用", 0.9))


def test_wide_boxes_for_foreign_text():
    img = np.zeros((1080, 1920, 3), np.uint8)
    seen = []
    champselect.is_champ_select(img, lambda crop: seen.append(crop.shape) or ("", 0.0))
    champselect.is_champ_select(img, lambda crop: seen.append(crop.shape) or ("", 0.0), marker="Available Champions")
    assert seen[1][1] > seen[0][1] * 3              # 外服用更宽的框


class _Resp:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        for i in range(0, len(self.data), n):
            yield self.data[i:i + n]


def test_langpack_download_verifies(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    data = b"x" * 1000
    pack = {"file": "k.onnx", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "urls": ["https://a.invalid/k", "https://b.invalid/k"]}
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return _Resp(b"bad" if url.startswith("https://a.") else data)  # 第一个地址给了坏文件
    monkeypatch.setattr(langpack.requests, "get", fake_get)
    seen = []
    assert langpack.installed(pack) is None
    path = langpack.download(pack, progress=lambda d, t: seen.append(d))
    assert calls == pack["urls"] and path.read_bytes() == data and langpack.installed(pack) == path
    assert seen[-1] == len(data) and not path.with_name("k.onnx.part").exists()
    monkeypatch.setattr(langpack.requests, "get", lambda url, **kw: _Resp(b"bad"))
    path.unlink()
    with pytest.raises(ValueError):
        langpack.download(pack)
    assert langpack.installed(pack) is None


def test_same_name_except_bang():
    """4.0 的「罪恶快感！」和老的「罪恶快感」去掉标点后同名：按有没有感叹号分。只剩一个字的名字（邦！）也认得。"""
    m = NameMatcher({"1322": "罪恶快感", "3004": "罪恶快感！", "2111": "邦！", "157": "亚索"})
    assert m.match("罪恶快感！")[0] == "3004" and m.match("罪恶快感!")[0] == "3004"
    assert m.match("罪恶快感")[0] == "1322"
    assert m.match("邦!")[0] == "2111" and m.match("邦")[0] == "2111"
    assert m.match("索")[0] is None                  # 一个字只认完全一样的


def test_old_traditional_setting_loads_as_chinese(tmp_path):
    from lolhex.config import Settings
    p = tmp_path / "config.json"
    p.write_text('{"game_lang": "zh_TW", "install_id": "0123456789abcdef"}', encoding="utf-8")
    assert Settings.load(p).game_lang == "zh_CN"
    p.write_text('{"game_lang": "xx", "install_id": "0123456789abcdef"}', encoding="utf-8")
    assert Settings.load(p).game_lang == "zh_CN"
