"""查自己的水平：名字#编号 → 区内前百分之几 + 分段。请求由官方模块发，公开源码里没有，见 official.py。

只返回前百分比、分段、近 30 天局数和数据日期，不返回分数。查询有次数限制。
"""

from __future__ import annotations

import re

import requests

from . import cloud, official

# 从游戏客户端复制的名字前后带看不见的方向控制符（⁦名字⁩#⁦编号⁩），不去掉永远查不到（10-23 日志里 5 个人反复查不到）
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁩﻿]")


class RankError(Exception):
    """查不了：消息直接给用户看。"""


def normalize(riot_id: str) -> str:
    """去掉看不见的字符；全角＃换成 #，# 两边的空格去掉（和官网查分页一样）。"""
    s = _INVISIBLE.sub("", riot_id or "").replace("＃", "#")
    return re.sub(r"\s*#\s*", "#", s).strip()


def _fraction(value) -> bool:
    return type(value) in (int, float) and 0 <= value <= 1


def valid_result(body) -> bool:
    """核对界面和本地缓存要读取的查分字段。"""
    if not isinstance(body, dict) or not isinstance(body.get("results"), list) or not body["results"]:
        return False
    if body.get("as_of") and not isinstance(body["as_of"], str):
        return False
    for row in body["results"]:
        if (not isinstance(row, dict) or not _fraction(row.get("top"))
                or not all(isinstance(row.get(name), str) for name in ("band", "band_name", "region_name"))
                or type(row.get("games")) is not int or row["games"] < 0):
            return False
        if row.get("solo_top") is not None and not _fraction(row["solo_top"]):
            return False
    return True


def lookup(riot_id: str, timeout: float = 10) -> dict:
    """返回 {"as_of", "results": [...]}；查不到或失败抛 RankError。"""
    riot_id = normalize(riot_id)
    name, sep, tag = riot_id.rpartition("#")
    if not sep or not name.strip() or not tag.strip():
        raise RankError("请填「名字#编号」，比如：提莫#12345")
    if not official.available():
        raise RankError("软件没装完整，查不了分：请重新下载安装包，覆盖安装一次" if official.broken()
                        else "这不是官方安装包，没有查分功能")
    cfg = cloud.get().get("rank") or {}
    if not cfg.get("enabled", True):
        raise RankError(cfg.get("message") or "查询暂时关闭，请稍后再试")
    try:
        status, body = official.rank(riot_id, cloud.sites(), timeout)
    except requests.RequestException:
        raise RankError("连不上查询服务，请稍后再试")
    body = body if isinstance(body, dict) else {}
    if status == 200 and valid_result(body):
        return body
    error = body.get("error")
    if isinstance(error, str) and error:
        raise RankError(error)
    raise RankError("查询次数用完了，请稍后再试" if status == 429
                    else "查询服务暂时不可用，请稍后再试" if status >= 500 else f"查询失败（{status}）")


def top_pct(x: float) -> str:
    v = x * 100
    return f"{v:.2f}%" if v < 1 else f"{v:.1f}%" if v < 10 else f"{v:.0f}%"


def describe(res: dict) -> str:
    """一句话：前 3.1%（高分）· 艾欧尼亚 · 近 30 天 120 局 · 数据截至 10-16"""
    if not res or not res.get("results"):
        return ""
    r = res["results"][0]
    s = f"前 {top_pct(r['top'])}（{r['band_name']}）· {r['region_name']} · 近 30 天 {r['games']} 局"
    if res.get("as_of"):
        s += f" · 数据截至 {res['as_of'][5:10]}"
    if r.get("solo_top"):
        s += f"<br>单排对局水平：相当于区内前 {top_pct(r['solo_top'])}（近 30 天单排 {r.get('solo_games') or 0} 局）"
    if len(res["results"]) > 1:
        s += "<br>另有：" + "、".join(f"{x['region_name']} {x['band_name']}" for x in res["results"][1:3])
    return s
