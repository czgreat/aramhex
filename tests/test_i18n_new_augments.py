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


def test_unlisted_augment_is_shown_by_its_riot_chinese_name(store):
    from lolhex import recommend
    store.augments.pop("3262", None)
    ev = recommend.evaluate(store, None, ["3262", None, None])
    assert ev["slots"][0]["name"] == "史莱姆时间"
