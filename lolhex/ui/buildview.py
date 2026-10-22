"""出装、召唤师技能的富文本（主窗口和浮窗共用）：装备和召唤师技能都只放图标。
图标还没下载好时先显示名字，下载完 IconCache.version 变化后界面重新渲染。"""

from __future__ import annotations
from html import escape

GRAY = "#8b93a7"


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _img(path: str, size: int) -> str:
    return f'<img src="{path}" width="{size}" height="{size}" align="middle">'


def item(icons, it: dict, size: int) -> str:
    """装备：只放图标（还没下载好时临时用名字）。"""
    p = icons.local("item", it.get("id"), it.get("icon")) if icons else None
    return _img(p, size) if p else (it.get("name") or it.get("id") or "")


def spell_pair(icons, row: dict, size: int) -> str:
    """召唤师技能组合：只放图标（没下载好时临时用名字）。"""
    out = []
    for s in row.get("spells") or []:
        p = icons.local("spell", s.get("id"), s.get("icon")) if icons and s.get("id") else None
        out.append(_img(p, size) if p else s.get("name", ""))
    return "&nbsp;".join(out)


def skills_line(d: dict, show_win: bool) -> str | None:
    """加点只写主副：「主 Q · 副 E」（不列逐级加点）。"""
    sk = d.get("skills") or []
    mx = [k for k in (sk[0].get("max") or []) if k in "QWE"] if sk else []
    if not mx:
        return None
    s = "<b>加点</b>　主 " + mx[0] + (" · 副 " + mx[1] if len(mx) > 1 else "")
    if show_win and sk[0].get("win") is not None:
        s += f" <span style='color:{GRAY}'>{_pct(sk[0]['win'])}</span>"
    return s


def lines(d: dict, icons, show_win: bool, size: int = 24, compact: bool = False) -> list[str]:
    """出装相关的几行：召唤师技能（有数据时）、核心三件（最多 3 套）、鞋子、出门装。compact=浮窗（图标小一号、行更紧凑）。"""
    b = d.get("builds") or {}
    wr = (lambda x: f" <span style='color:{GRAY}'>{_pct(x)}</span>") if show_win else (lambda x: "")
    out = []
    sums = d.get("summoners") or []  # panel.sort_summoners 已经筛好（出场 ≥10%、按胜率、最多两组）
    if sums and d.get("summoner_source"):
        out.append("<span style='color:" + GRAY + "'>召唤师技能：" + escape(d["summoner_source"])
                   + " · " + escape(str(d.get("summoner_source_date") or "日期未知")) + "</span>")
    def stat(s):  # 胜率在前（按胜率排的），排得下就带上出场；关了显示胜率只写出场
        return (f"胜率 {_pct(s.get('win'))} · " if show_win else "") + f"出场 {_pct(s.get('pick'))}"
    if sums and compact:  # 浮窗：一组一行，第一行带标题，第二行对齐
        for i, s in enumerate(sums):
            head = "<b>召唤师技能</b>　" if i == 0 else "<span style='color:transparent'><b>召唤师技能</b></span>　"
            out.append(head + spell_pair(icons, s, size) + f" <span style='color:{GRAY}'>{stat(s)}</span>")
    elif sums:
        out.append("<b>召唤师技能</b>")
        out.extend(f"　{spell_pair(icons, s, size)} <span style='color:{GRAY}'>{stat(s)}</span>" for s in sums)

    its = (d.get("item_stats") or [])[: 4 if compact else 6]
    if its:  # 自采统计的装备（数据里有 hero_items.json 才有）
        out.append("<b>常出装备</b>　" + "　".join(item(icons, x["item"], size) + wr(x.get("win")) for x in its))

    def core(c):
        return " → ".join(item(icons, x, size) for x in c.get("items") or []) + wr(c.get("win"))
    cores = b.get("core") or []
    if cores:
        out.append("<b>核心三件</b>")
        out.extend("　" + core(c) for c in cores[:3])
    shoes = (b.get("shoes") or [])[:2]
    if shoes:
        out.append("<b>鞋子</b>　" + "　".join(item(icons, x["item"], size) + wr(x.get("win")) for x in shoes))
    if b.get("start"):
        s = b["start"][0]
        out.append("<b>出门装</b>　" + " + ".join(item(icons, x, size) for x in s.get("items") or []) + wr(s.get("win")))
    return out
