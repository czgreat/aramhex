"""外服：各语言名字表、认字匹配、选英雄界面的字。"""
from types import SimpleNamespace

import numpy as np
import pytest

from lolhex import i18n
from lolhex.engine import augment_matcher
from lolhex.vision import champselect
from lolhex.vision.matcher import NameMatcher


def test_names_by_language():
    assert i18n.augment_names("zh_CN") == {} and i18n.hero_names("zh_CN") == {}  # 国服用腾讯的名字
    assert i18n.augment_names("en_US")["1305"] == "Leg Day"
    assert i18n.traditional_augment_names()["1305"] == "腿部訓練日"   # 繁中是另一套译名，不是简转繁
    assert i18n.normalize_lang("zh_TW") == "zh_CN" and i18n.augment_names("zh_TW") == {}  # 繁中并进了中文
    assert i18n.normalize_lang("ko_KR") == "zh_CN" and i18n.augment_names("ko_KR") == {}  # 1.1.4 起不支持韩服
    assert i18n.hero_names("ja_JP")["157"][0] == "ヤスオ"
    assert i18n.ui_text("bench", "zh_CN") == "可用"
    assert i18n.ui_text("bench", "en_US") == "Available Champions"
    assert i18n.normalize_lang("xx") == "zh_CN"
    assert i18n.ui_texts("bench", "zh_CN") == ["可用", "可使用的英雄"] and i18n.ui_texts("bench", "en_US") == ["Available Champions"]
    assert i18n.normalize_lang("zh_MY") == "zh_CN" and i18n.augment_names("zh_MY") == {}  # 马来西亚服并进了中文
    assert "马来西亚" in dict(i18n.LANGS)["zh_CN"]


def test_chinese_mode_reads_malaysia_names():
    """马来西亚服（拳头 zh_MY 简中）译名和腾讯八成不同：中文模式一起认（GitHub czgreat/aramhex#6）。"""
    extra = i18n.chinese_extra_augment_names()
    assert extra["1305"][0] == "腿部訓練日"                  # 繁中在前
    assert "扳机炼狱" not in extra.get("2005", []) and "狂袭地狱火" in extra["2005"]
    store = SimpleNamespace(mayhem_augments=lambda: {"2005": {"name": "扳机炼狱"}, "1322": {"name": "罪恶快感"},
                                                     "2076": {"name": "保持坚定"}, "1327": {"name": "坚若磐石"}})
    cn = augment_matcher(store, SimpleNamespace(game_lang="zh_CN", match_threshold=72.0))
    assert cn.match("狂袭地狱火")[0] == "2005" and cn.match("扳机炼狱")[0] == "2005"
    assert cn.match("狂躁！")[0] == "1322" and cn.match("罪恶快感")[0] == "1322"
    # zh_MY 里 2076 和已下线的 1327 同名「坚定不移」：名字表只给还在玩的 2076
    assert cn.match("坚定不移")[0] == "2076" and cn.match("坚若磐石")[0] == "1327"
    heroes = i18n.chinese_extra_hero_names()
    assert "厄萨斯" in heroes["266"] and "冥血剑魔" in heroes["266"]   # 亚托克斯在马来西亚服的名字、称号


def test_malaysia_names_do_not_collide_or_change_existing_matches(tmp_path, no_network):
    """内置真名单：zh_MY 名字不能和别的海克斯 / 英雄同名，每个都认成自己；并进来以后，现有名字（腾讯、繁中、OCR 别名）认出的结果一个不变。"""
    from lolhex.data.store import DataStore
    from lolhex.vision.matcher import normalize
    store = DataStore(tmp_path)
    assert len(store.mayhem_augments()) > 300 and len(store.heroes) > 170
    settings = SimpleNamespace(game_lang="zh_CN", match_threshold=72.0)
    with_my = augment_matcher(store, settings)
    mp = pytest.MonkeyPatch()
    mp.setattr(i18n, "EXTRA_ZH", (i18n.TW,))
    try:
        before = augment_matcher(store, settings)
    finally:
        mp.undo()
    ids = set(store.mayhem_augments())
    my = {aid: n for aid, n in i18n._augments(i18n.MY).items() if aid in ids}
    assert len(my) > 200
    owner: dict[str, set] = {}
    for aid, key in zip(with_my._ids, with_my._keys):
        owner.setdefault(key, set()).add(aid)
    for aid, n in my.items():
        assert owner[normalize(n)] == {aid}, (aid, n, owner[normalize(n)])
        assert with_my.match(n)[0] == aid, (aid, n)
    for key in before._keys:
        assert with_my.match(key)[0] == before.match(key)[0], key
    # 英雄：zh_MY 名字 / 称号不和别的英雄同名
    hero_keys: dict[str, set] = {}
    for hid, h in store.heroes.items():
        for n in [h.get("name"), h.get("nick")] + i18n.chinese_extra_hero_names().get(hid, []):
            if n:
                hero_keys.setdefault(normalize(n), set()).add(hid)
    for hid, ns in i18n._heroes(i18n.MY).items():
        for n in ns:
            assert hero_keys[normalize(n)] == {hid}, (hid, n, hero_keys[normalize(n)])


def test_augment_matcher_uses_game_language():
    store = SimpleNamespace(mayhem_augments=lambda: {"1305": {"name": "练腿日"}, "1195": {"name": "巨人杀手"}})
    cn = augment_matcher(store, SimpleNamespace(game_lang="zh_CN", match_threshold=72.0))
    assert cn.match("练腿日")[0] == "1305" and cn.match("Leg Day")[0] is None
    assert cn.match("腿部訓練日")[0] == "1305"      # 中文模式也认繁中（台服）的名字
    en = augment_matcher(store, SimpleNamespace(game_lang="en_US", match_threshold=72.0))
    assert en.match("Leg Day")[0] == "1305" and en.match("Giant Slayer")[0] == "1195"


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


def test_whole_screen_ocr_skips_one_char_lines(store, tmp_path, monkeypatch):
    """头像库没下载好时整屏认字：屏幕上单独一个字（聊天、玩家名里的）不当英雄名，固定位置的小块里照常认。"""
    import numpy as np

    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.vision import ocr
    from lolhex.vision.ocr import TextLine
    eng = Engine(Settings(), home=tmp_path, store=store)
    m = NameMatcher({"98": "慎", "157": "亚索"}, 86.0)
    monkeypatch.setattr(eng, "_hero_matcher", lambda: m)
    lines = [TextLine("慎", 0.9, (0, 0, 60, 60)), TextLine("亚索", 0.9, (0, 100, 80, 130))]
    monkeypatch.setattr(ocr.OCR, "get", classmethod(lambda cls: type("O", (), {"detect": lambda self, img: lines})()))
    picked = []
    monkeypatch.setattr(eng, "_set_hero_auto", lambda hid, src: picked.append(hid))
    eng._champ_select_ocr(np.zeros((200, 200, 3), np.uint8))
    assert picked == ["157"]                         # 字最大的「慎」没算，认的是亚索
    assert eng._hero_by_name("慎") == "98"           # 我方每行、英雄卡的小块里一个字的名字照常认


def test_augment_ocr_aliases():
    """OCR 常读错的海克斯名（augment_aliases.json，Gitea #35）：4 个字错一个只有 50 分，过不了门槛。"""
    store = SimpleNamespace(mayhem_augments=lambda: {"1005": {"name": "喂呜喂呜"}, "1063": {"name": "狂徒豪气"},
                                                     "1420": {"name": "咏叹奏鸣"}})
    m = augment_matcher(store, SimpleNamespace(game_lang="zh_CN", match_threshold=72.0))
    assert m.match("喂鸣喂鸣") == ("1005", 100.0) and m.match("喂呜喂呜")[0] == "1005"
    assert m.match("侠盗恆毅")[0] == "1063" and m.match("俠盜恆毅")[0] == "1063" and m.match("狂徒豪气")[0] == "1063"
    assert m.match("咏叹奏鸣")[0] == "1420"
    # 别名只加给名字表里有的海克斯
    m = augment_matcher(SimpleNamespace(mayhem_augments=lambda: {"1420": {"name": "咏叹奏鸣"}}),
                        SimpleNamespace(game_lang="zh_CN", match_threshold=72.0))
    assert m.match("喂鸣喂鸣")[0] is None
