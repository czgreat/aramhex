"""16.21 起的新海克斯：台服、马来西亚服译名完全不同；测试服新海克斯腾讯资料还没有。"""

from lolhex import i18n


def test_name_table_has_16_22_augments_in_every_chinese_client():
    t = i18n._table()["augments"]
    assert t["3060"]["zh_TW"] == "一片歌手" and t["3060"]["zh_MY"] == "一曲红人"   # 国服「单曲红人」
    assert t["3159"]["zh_TW"] == "崛起"                                           # 国服「登峰造极境」
    assert "2128" in t  # 拳头已删、腾讯资料还列着的海克斯：旧名字保留


def test_chinese_mode_reads_traditional_and_unlisted_names(store):
    from lolhex.config import Settings
    from lolhex.engine import augment_matcher
    store.augments.setdefault("3060", {"name": "单曲红人", "modes": ["KIWI"], "rarity": "gold"})
    store.augments.pop("3262", None)  # 测试服新海克斯：腾讯资料没有
    m = augment_matcher(store, Settings())
    assert m.match("一片歌手")[0] == "3060"
    assert m.match("单曲红人")[0] == "3060"
    assert m.match("史莱姆时间")[0] == "3262"
    assert m.match("史萊姆時間")[0] == "3262"
    assert "3176" not in m._ids  # 五杀乐队的内部标记照旧不认


def _matcher(tmp_path):
    from types import SimpleNamespace
    from lolhex.data.store import DataStore
    from lolhex.engine import augment_matcher
    store = DataStore(tmp_path)
    return store, augment_matcher(store, SimpleNamespace(game_lang="zh_CN", match_threshold=72.0))


def test_every_augment_of_every_mode_reads_as_itself(tmp_path, no_network):
    """海斗每个模式的海克斯，国服、台服、马来西亚服的名字都认成自己。"""
    _, m = _matcher(tmp_path)
    t = i18n._table()
    bad = [(aid, lang, n, m.match(n)) for ids in t["pools"].values() for aid in ids for lang in ("zh_CN", "zh_TW", "zh_MY")
           if (n := t["augments"].get(aid, {}).get(lang)) and m.match(n)[0] != aid]
    assert {b[0] for b in bad} == {"1424"}  # 「？？？」全是标点，认不了


def test_classic_mode_augments_in_taiwan_and_malaysia_clients(tmp_path, no_network):
    _, m = _matcher(tmp_path)
    for text, aid in {"升級：冥火之擁": "7007", "時間扭曲：雄心之鋼": "7010", "真實永恆": "7011", "升级：支配宝匣": "7009",
                      "普罗王大弹跳": "2003", "空降水豚": "1414", "卡皮巴拉空投": "1414"}.items():
        assert m.match(text)[0] == aid, text


def test_ocr_readings_of_traditional_names(tmp_path, no_network):
    """日志里的读法：整词读错的用别名，繁体字读成简体字的按繁简统一后一字不差认。"""
    _, m = _matcher(tmp_path)
    for text, aid in {"鳴咿鳴咿": "1005", "秦鳴曲": "1420", "秦鸣曲": "1420", "奏嗚曲": "1420", "鸣鸣警报": "1005", "别乏眼": "1026", "杀参時間": "1211",
                      "閃光侠": "1116", "衝锋": "1019", "亮起来！": "1051", "捣蛋鬼": "1347"}.items():
        assert m.match(text)[0] == aid, text
    assert m.exact("閃光侠") is None and m.exact("閃光俠") == "1116"  # 不算名字完全对上的证据
    # 商店、装备里的字繁简统一后只是像某个海克斯，照旧不认
    for text in ("狂战士胫甲", "中娅沙漏", "法力强化"):
        assert m.match(text)[0] is None, text


def test_trad_fallback_changes_no_existing_result(tmp_path, no_network, monkeypatch):
    _, now = _matcher(tmp_path)
    monkeypatch.setattr(i18n, "trad_chars", lambda: {})
    _, before = _matcher(tmp_path)
    texts = {k[:i] + k[i + 1:] for k in before._keys for i in range(len(k))} | set(before._keys)
    for text in texts:
        if (hit := before.match(text))[0]:
            assert now.match(text) == hit, text
    assert i18n._table()["trad"] and before._folded == {}


def test_items_heroes_and_spells_are_not_read_as_augments_by_trad_fallback(tmp_path, no_network, monkeypatch):
    """简中装备「救赎」、英雄称号「狂战士」和台服海克斯「救贖」「狂戰士」只差繁简。"""
    import json
    from pathlib import Path
    _, now = _matcher(tmp_path)
    items = json.loads((Path(i18n.__file__).with_name("data") / "bundled" / "item_metadata.json").read_text(encoding="utf-8"))
    texts = {x["name"] for x in items.values()} | {n for v in i18n._table()["heroes"].values() for lang in ("zh_CN", "zh_MY") for n in v.get(lang, ())}
    assert {"救赎", "狂战士"} <= texts
    monkeypatch.setattr(i18n, "trad_chars", lambda: {})
    _, before = _matcher(tmp_path)
    assert [t for t in sorted(texts) if now.match(t)[0] != before.match(t)[0]] == []
    for text in ("救赎", "狂战士", "治疗术"):
        assert now.match(text)[0] is None, text
    assert (now.match("救贖")[0], now.match("狂戰士")[0], now.match("激光治疗术")[0]) == ("3039", "2009", "1339")


def test_update_script_lists_renamed_names_and_collisions():
    from scripts import update_i18n as u
    old = {"1": {"zh_CN": "甲", "zh_TW": "乙"}, "2": {"zh_CN": "丙"}}
    assert u.renamed(old, {**old, "3": {"zh_CN": "丁"}}) == []
    assert u.renamed(old, {"1": {"zh_CN": "甲", "zh_TW": "戊"}}) == [("1", "zh_TW", "乙", "戊"), ("2", "zh_CN", "丙", None)]
    aug = {"1": {"zh_TW": "救贖", "zh_CN": "拯救"}, "2": {"zh_TW": "殘忍", "zh_MY": "残忍"}}
    assert u.trad_collisions(aug, {"贖": "赎", "殘": "残"}, ["救赎", "救贖", "残忍", "鞋子"]) == ["救赎"]


def test_same_folded_name_is_not_guessed():
    from lolhex.vision.matcher import NameMatcher
    m = NameMatcher({"1": "發財", "2": "发財"}, trad={"發": "发", "財": "财"})
    assert m.match("发财")[0] is None
    assert NameMatcher({"1": "發財"}, trad={"發": "发", "財": "财"}).match("发财") == ("1", 100.0)
    assert NameMatcher({"1": "發財"}).match("发财")[0] is None


def test_new_modes_are_picked_up_and_missing_ones_stop_the_script():
    import pytest
    from scripts import update_i18n as u
    files = [{"name": n} for n in ("kiwi.bin", "kiwi.bin.json", "kiwi_jade.bin.json", "kiwi_new.bin.json", "cherry.bin.json")]
    assert u.mode_files(files) == ["kiwi.bin.json", "kiwi_jade.bin.json", "kiwi_new.bin.json"]
    with pytest.raises(SystemExit):
        u.mode_files([{"name": "kiwi.bin.json"}])


def test_malaysia_same_name_stays_with_its_previous_owner():
    from scripts.update_i18n import drop_my_duplicates
    make = lambda: {a: {"zh_MY": "坚定不移"} for a in ("1327", "2076", "3263")}
    aug = make()
    drop_my_duplicates(aug, {"2076", "3263"}, {"2076": {"zh_MY": "坚定不移"}, "3263": {}})
    assert [a for a, v in aug.items() if "zh_MY" in v] == ["2076"]
    aug = make()
    drop_my_duplicates(aug, {"2076", "3263"})
    assert not any("zh_MY" in v for v in aug.values())


def test_unlisted_augment_is_shown_by_its_riot_chinese_name(store):
    from lolhex import recommend
    store.augments.pop("3262", None)
    ev = recommend.evaluate(store, None, ["3262", None, None])
    assert ev["slots"][0]["name"] == "史莱姆时间"
