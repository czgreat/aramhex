"""查自己的水平：名字#编号 → 区内前百分之几 + 分段（aramhex.com/api/rank）。

显示个人总分、区内排名、样本提示及单排对局水平。查询有次数限制。
"""

from __future__ import annotations

import re
from html import escape

import requests

from . import __version__, cloud

# 从游戏客户端复制的名字前后带看不见的方向控制符（⁦名字⁩#⁦编号⁩），不去掉永远查不到（10-23 日志里 5 个人反复查不到）
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁩﻿]")


class RankError(Exception):
    """查不了：消息直接给用户看。"""


def normalize(riot_id: str) -> str:
    """去掉看不见的字符；全角＃换成 #，# 两边的空格去掉（和官网查分页一样）。"""
    s = _INVISIBLE.sub("", riot_id or "").replace("＃", "#")
    return re.sub(r"\s*#\s*", "#", s).strip()


def lookup(riot_id: str, timeout: float = 10) -> dict:
    """返回 {"as_of", "results": [...]}；查不到或失败抛 RankError。"""
    riot_id = normalize(riot_id)
    name, sep, tag = riot_id.rpartition("#")
    if not sep or not name.strip() or not tag.strip():
        raise RankError("请填「名字#编号」，比如：提莫#12345")
    cfg = cloud.get().get("rank") or {}
    if not cfg.get("enabled", True):
        raise RankError(cfg.get("message") or "查询暂时关闭，请稍后再试")
    try:
        r = requests.get(cfg.get("api") or cloud.DEFAULT["rank"]["api"], params={"id": riot_id},
                         headers={"X-AramHex": __version__, "X-AramHex-Score-Policy":"v2", "User-Agent": f"aramhex/{__version__}"}, timeout=timeout)
    except requests.RequestException:
        raise RankError("连不上查询服务，请稍后再试")
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 200 and body.get("results"):
        return body
    raise RankError(body.get("error") or ("查询服务暂时不可用，请稍后再试" if r.status_code >= 500 else f"查询失败（{r.status_code}）"))


def _pct(x: float) -> str:
    v = x * 100
    return f"{v:.2f}%" if v < 1 else f"{v:.1f}%" if v < 10 else f"{v:.0f}%"


def describe(res: dict) -> str:
    """一句话：前 3.1%（高分）· 艾欧尼亚 · 近 30 天 120 局 · 数据截至 10-16"""
    if not res or not res.get("results"):
        return ""
    parts = []
    for r in res['results']:
        s = summary(r) + f"· {escape(str(r['region_name']))} · 近 30 天 {r['games']} 局"
        if r.get('solo_top') is not None:
            s += f"<br>单排对局水平：相当于区内前 {_pct(r['solo_top'])}"
        parts.append(s)
    s = '<br><br>'.join(parts)
    if res.get("as_of"):
        s += f" · 数据截至 {res['as_of'][5:10]}"
    return s


def summary(r: dict) -> str:
    if r.get('score_eligible') is False or r.get('top') is None:
        return '样本较少'
    prefix = f"个人总分 {r['score']:.1f} 分 · " if r.get('score') is not None else ''
    text = prefix + f"前 {_pct(r['top'])}（{escape(str(r['band_name']))}）"
    if r.get('games',31) <= 30:
        text += ' · 样本较少'
    return text
