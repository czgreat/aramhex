"""外服：游戏客户端是别的语言时，海克斯名、英雄名、选英雄界面上的字按那种语言认。

名字表 data/i18n.json 由 scripts/update_i18n.py 从拳头公开数据生成，ID 和腾讯同一套，
所以统计数据、界面显示（中文名）都不用改，只是认字时多一套名字。
韩文要另外的文字识别模型（vision/models/ 下，打包时下载），其他语言内置模型就认得。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

PATH = Path(__file__).resolve().parent / "data" / "i18n.json"
# 设置里的选项：(代码, 显示名)。国服是 zh_CN，名字用腾讯的（和拳头简中一致）
LANGS = [("zh_CN", "简体中文（国服）"), ("zh_TW", "繁體中文（台服等）"), ("en_US", "English（美服、欧服等）"),
         ("ja_JP", "日本語（日服）"), ("ko_KR", "한국어（韩服）")]
LANG_CODES = {code for code, _ in LANGS}
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


def augment_names(lang: str) -> dict[str, str]:
    """{海克斯 ID: 这种语言的名字}；国服返回空（用腾讯的名字）。"""
    lang = normalize_lang(lang)
    if lang == "zh_CN":
        return {}
    return {aid: names[lang] for aid, names in (_table().get("augments") or {}).items() if names.get(lang)}


def hero_names(lang: str) -> dict[str, list[str]]:
    """{英雄 ID: [名字, 称号]}；国服返回空。"""
    lang = normalize_lang(lang)
    if lang == "zh_CN":
        return {}
    return {hid: [n for n in names[lang] if n] for hid, names in (_table().get("heroes") or {}).items()
            if names.get(lang)}


def ui_text(key: str, lang: str) -> str:
    """选英雄界面上的字：bench=预选栏左边的「可用」，pick=开局选卡的「选择你的英雄」。"""
    lang = normalize_lang(lang)
    if lang == "zh_CN":
        return UI_CN[key]
    return ((_table().get("ui") or {}).get(key) or {}).get(lang) or UI_CN[key]
