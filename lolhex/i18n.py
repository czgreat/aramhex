"""外服：游戏客户端是别的语言时，海克斯名、英雄名、选英雄界面上的字按那种语言认。

名字表 data/i18n.json 由 scripts/update_i18n.py 从拳头公开数据生成，ID 和腾讯同一套，
所以统计数据、界面显示（中文名）都不用改，只是认字时多一套名字。
这几种语言内置的识别模型都认得。韩服（ko_KR）1.1.4 起不再支持，以前选过的按中文处理。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

PATH = Path(__file__).resolve().parent / "data" / "i18n.json"
# 设置里的选项：(代码, 显示名)。中文是 zh_CN：简体名字用腾讯的（和拳头简中一致），
# 繁中（台服等）、马来西亚服简中（zh_MY，拳头另一套译名，和腾讯八成不同）的名字也一起认——
# 这些玩家多半不会去改设置。1.1.4 查过：并进来后不同海克斯 / 英雄没有同名，现有名字的识别结果不变。
# 以前单独有过繁中选项（zh_TW）和韩服（ko_KR），存着这两个值的当中文
LANGS = [("zh_CN", "中文 简-繁（国服、台服、马来西亚服）"), ("en_US", "English（美服、欧服等）"),
         ("ja_JP", "日本語（日服）")]
LANG_CODES = {code for code, _ in LANGS}
TW = "zh_TW"
MY = "zh_MY"
EXTRA_ZH = (TW, MY)  # 中文模式额外认的名字
# 国服客户端选英雄界面上的字（i18n.json 里也有，这里写死，名字表缺失时国服照常能用）
UI_CN = {"bench": "可用", "pick": "选择你的英雄"}


def normalize_lang(lang: str | None) -> str:
    return lang if lang in LANG_CODES else "zh_CN"


@lru_cache(maxsize=1)
def _table() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("外服名字表读不到：%s", PATH)
        return {}


def _augments(lang: str) -> dict[str, str]:
    return {aid: names[lang] for aid, names in (_table().get("augments") or {}).items() if names.get(lang)}


def _heroes(lang: str) -> dict[str, list[str]]:
    return {hid: [n for n in names[lang] if n] for hid, names in (_table().get("heroes") or {}).items()
            if names.get(lang)}


def augment_names(lang: str) -> dict[str, str]:
    """{海克斯 ID: 这种语言的名字}；中文返回空（用腾讯的名字，繁中另见 traditional_augment_names）。"""
    lang = normalize_lang(lang)
    return {} if lang == "zh_CN" else _augments(lang)


def hero_names(lang: str) -> dict[str, list[str]]:
    """{英雄 ID: [名字, 称号]}；中文返回空。"""
    lang = normalize_lang(lang)
    return {} if lang == "zh_CN" else _heroes(lang)


def traditional_augment_names() -> dict[str, str]:
    """中文模式下额外认的繁中海克斯名。"""
    return _augments(TW)


def traditional_hero_names() -> dict[str, list[str]]:
    """中文模式下额外认的繁中英雄名和称号。"""
    return _heroes(TW)


def chinese_extra_augment_names() -> dict[str, list[str]]:
    """中文模式下额外认的海克斯名：{ID: [繁中, 马来西亚服简中]}（有哪个放哪个，去重）。"""
    out: dict[str, list[str]] = {}
    for lang in EXTRA_ZH:
        for aid, n in _augments(lang).items():
            if n not in out.setdefault(aid, []):
                out[aid].append(n)
    return out


def chinese_extra_hero_names() -> dict[str, list[str]]:
    """中文模式下额外认的英雄名和称号（繁中 + 马来西亚服简中）。"""
    out: dict[str, list[str]] = {}
    for lang in EXTRA_ZH:
        for hid, ns in _heroes(lang).items():
            out.setdefault(hid, []).extend(n for n in ns if n not in out[hid])
    return out


def ui_text(key: str, lang: str) -> str:
    """选英雄界面上的字：bench=预选栏左边的「可用」，pick=开局选卡的「选择你的英雄」。"""
    lang = normalize_lang(lang)
    if lang == "zh_CN":
        return UI_CN[key]
    return ((_table().get("ui") or {}).get(key) or {}).get(lang) or UI_CN[key]


def ui_texts(key: str, lang: str) -> list[str]:
    """同上，中文模式是 [简体, 繁中, 马来西亚服] 几种写法（去重），先试简体。"""
    out = [ui_text(key, lang)]
    if normalize_lang(lang) == "zh_CN":
        for extra in EXTRA_ZH:
            t = ((_table().get("ui") or {}).get(key) or {}).get(extra)
            if t and t not in out:
                out.append(t)
    return out
