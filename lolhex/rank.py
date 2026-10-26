"""查自己的水平：名字#编号 → 区内前百分之几 + 分段（aramhex.com/api/rank，家里服务器经 DMIT 中转）。

只返回前百分比、分段、近 30 天局数和数据日期，不返回分数。查询有次数限制。
"""

from __future__ import annotations

import re

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
                         headers={"X-AramHex": __version__, "User-Agent": f"aramhex/{__version__}"}, timeout=timeout)
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
    r = res["results"][0]
    s = f"前 {_pct(r['top'])}（{r['band_name']}）· {r['region_name']} · 近 30 天 {r['games']} 局"
    if res.get("as_of"):
        s += f" · 数据截至 {res['as_of'][5:10]}"
    if r.get("solo_top"):
        s += f"<br>单排对局水平：相当于区内前 {_pct(r['solo_top'])}（近 30 天单排 {r.get('solo_games') or 0} 局）"
    if len(res["results"]) > 1:
        s += "<br>另有：" + "、".join(f"{x['region_name']} {x['band_name']}" for x in res["results"][1:3])
    return s
