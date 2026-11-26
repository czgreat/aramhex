"""外服用的名字表：海克斯名、英雄名和选英雄界面上的字，英文、日文、繁中、马来西亚服简中（外加简中用来核对）。

    python scripts/update_i18n.py [--out lolhex/data/i18n.json]

来源都是拳头公开数据：
- 海克斯：CommunityDragon 的 kiwi*.bin.json（AugmentPlatformId 就是腾讯的海克斯 ID）+ 各语言 lol.stringtable.json。
  海斗的每个模式一个文件（kiwi 是常规海斗，kiwi_jade 是经典海斗），目录里有几个读几个；各模式的海克斯记在 pools，发版检查要用。
  正式服（latest）和测试服（pbe）都取，正式服优先；测试服补上还没上线的新海克斯。
  stringtable 里新加的键是哈希：小写键名的 xxh3_64 取低 38 位，写成 {%010x}（要装 xxhash，只有这个脚本用）。
- 繁简对照 trad：繁中海克斯名里的字 → 简体（要装 opencc-python-reimplemented，只有这个脚本用），OCR 把繁体字读成简体时用。
- trad_skip：繁简统一后和某个海克斯同名的简中装备、英雄、召唤师技能名（装备「救赎」和台服海克斯「救贖」），这几个不做繁简统一。
- 英雄：Data Dragon 各语言 champion.json 的 name 和 title（按 key 即英雄数字 ID）。
- 选英雄界面：rcp-fe-lol-champ-select 的 trans.json（预选栏左边的「可用」、开局选卡的「选择你的英雄」）。
拳头的简中名字和腾讯的应当一致，不一致的条数会打印出来。
已有海克斯、英雄的名字被改掉或删掉时列出来并停下，不写文件；确认过要改才加 --allow-rename。
发版检查还会拿上一个正式版再比一次，要改的写进 check_release_data.py 的 FOREIGN_RENAMED。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CD = "https://raw.communitydragon.org"
DD = "https://ddragon.leagueoflegends.com"
# 程序里的语言代码 → CommunityDragon 目录名。zh_MY 是马来西亚服的简中，译名和腾讯不同（更接近台服），中文模式一起认
LANGS = {"zh_CN": "zh_cn", "zh_TW": "zh_tw", "zh_MY": "zh_my", "en_US": "en_us", "ja_JP": "ja_jp"}
# 不收的名字。1339 的马来西亚名「激光治疗术」会让召唤师技能「治疗术」过线；简中名「激光治疗」已经能认出它
SKIP_NAMES = {("1339", "zh_MY")}
UI_KEYS = {"bench": "champion_bench_label", "pick": "subset-champions-grid-header"}


def get_json(url: str):
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.json()


def lookup(table: dict, key: str) -> str | None:
    k = key.lower()
    if k in table:
        return table[k]
    import xxhash
    for fn in (xxhash.xxh3_64_intdigest, xxhash.xxh64_intdigest):
        for bits in (38, 40):
            h = "{%010x}" % (fn(k.encode()) & ((1 << bits) - 1))
            if h in table:
                return table[h]
    return None


def mode_files(listing: list[dict]) -> list[str]:
    """目录列表里海斗各模式的文件：kiwi.bin.json、kiwi_jade.bin.json……以后出新模式自动带上。"""
    names = sorted(x["name"] for x in listing if x["name"].startswith("kiwi") and x["name"].endswith(".bin.json"))
    if not {"kiwi.bin.json", "kiwi_jade.bin.json"} <= set(names):
        raise SystemExit(f"海斗模式文件不全，只有 {names}；拳头改了文件名就改这里，不要跳过")
    return names


def pool(mode: dict) -> dict[str, str]:
    """一个模式里的海克斯：{ID: 名字的键}。"""
    return {str(v["AugmentPlatformId"]): v["NameTra"] for v in mode.values()
            if isinstance(v, dict) and v.get("__type") == "AugmentData" and v.get("AugmentPlatformId") and v.get("NameTra")}


def augments(branch: str) -> tuple[dict[str, dict[str, str]], dict[str, list[str]]]:
    """({ID: {语言: 名字}}, {模式: [ID]})。"""
    base = "game/maps/modespecificdata"
    tra: dict[str, str] = {}
    pools: dict[str, list[str]] = {}
    for f in mode_files(get_json(f"{CD}/json/{branch}/{base}/")):
        got = pool(get_json(f"{CD}/{branch}/{base}/{f}"))
        pools[f.removesuffix(".bin.json")] = sorted(got, key=int)
        for aid, key in got.items():
            tra.setdefault(aid, key)
    out: dict[str, dict[str, str]] = {}
    for lang, d in LANGS.items():
        table = get_json(f"{CD}/{branch}/game/{d}/data/menu/en_us/lol.stringtable.json")["entries"]
        for aid, key in tra.items():
            name = lookup(table, key)
            if name and name.strip() and (aid, lang) not in SKIP_NAMES:
                out.setdefault(aid, {})[lang] = name.strip()
    print(f"{branch}: {', '.join(f'{k} {len(v)} 个' for k, v in pools.items())}；合起来有名字的 {len(out)} 个")
    return out, pools


def trad_map(aug: dict[str, dict[str, str]]) -> dict[str, str]:
    """繁中海克斯名里用到的字 → 简体，只收写法不同的。"""
    from opencc import OpenCC
    conv = OpenCC("t2s")
    chars = sorted({c for names in aug.values() for c in names.get("zh_TW", "")})
    return {c: s for c in chars if len(s := conv.convert(c)) == 1 and s != c}


def other_names(hero: dict[str, dict[str, list[str]]]) -> set[str]:
    """简中（国服、马来西亚服）里装备、英雄、召唤师技能的名字，正式服和测试服都取。"""
    out = {n for langs in hero.values() for lang in ("zh_CN", "zh_MY") for n in langs.get(lang, ())}
    for branch in ("latest", "pbe"):
        for lang in ("zh_CN", "zh_MY"):
            for f in ("items", "summoner-spells"):
                rows = get_json(f"{CD}/{branch}/plugins/rcp-be-lol-game-data/global/{LANGS[lang]}/v1/{f}.json")
                out |= {x["name"] for x in rows if x.get("name")}
    return out


def trad_collisions(aug: dict[str, dict[str, str]], trad: dict[str, str], others) -> list[str]:
    """others 里繁简统一后和某个海克斯的中文名一样、本身又不是海克斯名的。"""
    sys.path.insert(0, str(ROOT))
    from lolhex.vision.matcher import normalize
    tab = str.maketrans(trad)
    names = {normalize(n) for v in aug.values() for lang, n in v.items() if lang.startswith("zh_")}
    folded = {n.translate(tab) for n in names}
    return sorted(n for n in set(others) if (k := normalize(n)) not in names and k.translate(tab) in folded)


def renamed(old: dict, new: dict) -> list[tuple]:
    """旧表里有、新表里改了或没了的名字（海克斯或英雄）：[(ID, 语言, 旧名, 新名)]。"""
    return [(aid, lang, n, (new.get(aid) or {}).get(lang)) for aid, v in old.items() for lang, n in v.items()
            if (new.get(aid) or {}).get(lang) != n]


def drop_my_duplicates(aug: dict[str, dict[str, str]], live: set[str], old: dict | None = None) -> list[tuple]:
    """zh_MY 里几个海克斯同名（2076「保持坚定」、3263「超坚定风采」和 1327「坚若磐石」都叫「坚定不移」）时，
    只给内置统计里还在玩的那个留 zh_MY 名字；还在玩的有几个就留给旧表里本来有的那个（认字结果不变）；
    分不出来就都不留。返回去掉的 [(名字, [ID...], 留下的 ID)]。"""
    sys.path.insert(0, str(ROOT))
    from lolhex.vision.matcher import normalize
    by_name: dict[str, list[str]] = {}
    for aid, names in aug.items():
        if names.get("zh_MY"):
            by_name.setdefault(normalize(names["zh_MY"]), []).append(aid)
    dropped = []
    for key, aids in by_name.items():
        if len(aids) < 2:
            continue
        keep = [a for a in aids if a in live]
        if len(keep) > 1:
            keep = [a for a in keep if ((old or {}).get(a) or {}).get("zh_MY")]
        keep = keep if len(keep) == 1 else []
        for a in aids:
            if a not in keep:
                aug[a].pop("zh_MY", None)
        dropped.append((key, sorted(aids), keep[0] if keep else None))
    return dropped


def heroes() -> tuple[str, dict[str, dict[str, list[str]]]]:
    ver = get_json(f"{DD}/api/versions.json")[0]
    out: dict[str, dict[str, list[str]]] = {}
    for lang in LANGS:
        for c in get_json(f"{DD}/cdn/{ver}/data/{lang}/champion.json")["data"].values():
            out.setdefault(str(c["key"]), {})[lang] = [c["name"], c["title"]]
    return ver, out


def ui_texts() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {k: {} for k in UI_KEYS}
    for lang, d in LANGS.items():
        t = get_json(f"{CD}/latest/plugins/rcp-fe-lol-champ-select/global/{d}/trans.json")
        for k, key in UI_KEYS.items():
            if t.get(key):
                out[k][lang] = t[key]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="外服名字表")
    ap.add_argument("--out", type=Path, default=ROOT / "lolhex" / "data" / "i18n.json")
    ap.add_argument("--tencent", type=Path, help="腾讯 kiwi_augments.json（核对简中名字；不给就在线取）")
    ap.add_argument("--allow-rename", action="store_true", help="已有海克斯的名字可以改掉或删掉（先看过列出来的再加）")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    before = json.loads(a.out.read_text(encoding="utf-8")) if a.out.exists() else {}
    old = before.get("augments") or {}
    aug, _ = augments("pbe")
    names, pools = augments("latest")
    aug.update(names)  # 正式服的名字优先
    live = set(json.loads((ROOT / "lolhex/data/bundled/augments.json").read_text(encoding="utf-8")))
    print("zh_MY 同名处理（名字, ID, 留给）：", drop_my_duplicates(aug, live, old))
    ver, hero = heroes()
    ui = ui_texts()

    tx = (json.loads(a.tencent.read_text(encoding="utf-8")) if a.tencent else
          get_json("https://game.gtimg.cn/images/lol/act/img/js/kiwi/kiwi_augments.json"))
    tx = {str(x["augmentID"]): x["name_cn"] for x in tx}
    diff = [(i, tx[i], v["zh_CN"]) for i, v in aug.items() if i in tx and v.get("zh_CN") and v["zh_CN"] != tx[i]]
    print(f"简中名字和腾讯一致 {sum(1 for i, v in aug.items() if tx.get(i) == v.get('zh_CN'))} 个，不一致 {len(diff)} 个", diff[:10])
    print("选英雄界面：", json.dumps(ui, ensure_ascii=False))
    # 拳头删掉的海克斯（如 10-26 热更新那批）腾讯资料可能还列着：旧表里的名字保留，不删
    if old:
        kept = {aid: names for aid, names in old.items() if aid not in aug}
        aug.update(kept)
        print(f"保留旧表里拳头已删的海克斯 {len(kept)} 个")

    if (changed := renamed(old, aug) + renamed(before.get("heroes") or {}, hero)):
        print(f"已有的名字变了 {len(changed)} 处（ID, 语言, 旧名, 新名）：", *changed, sep="\n  ")
        if not a.allow_rename:
            print("没有写文件。逐条确认后加 --allow-rename；旧名字还会在旧客户端或别的模式里出现的，加到 augment_aliases.json")
            return 1
    trad = trad_map(aug)
    skip = trad_collisions(aug, trad, other_names(hero))
    print("繁简统一不认：", skip)

    out = {"_说明": "外服名字表，scripts/update_i18n.py 生成。augments/heroes 按腾讯同一套 ID；heroes 每种语言是 [名字, 称号]。"
                    "zh_MY（马来西亚服简中）1.1.4 加入，中文模式和繁中一起认。拳头已删的海克斯保留旧名字。"
                    "pools 是正式服各模式的海克斯，trad 是繁简对照，trad_skip 是不做繁简统一的装备等名字。",
           "ddragon": ver, "pools": pools, "trad": trad, "trad_skip": skip, "augments": dict(sorted(aug.items(), key=lambda kv: int(kv[0]))),
           "heroes": dict(sorted(hero.items(), key=lambda kv: int(kv[0]))), "ui": ui}
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=0, sort_keys=False) + "\n", encoding="utf-8")
    print(f"写入 {a.out}：海克斯 {len(aug)} 个、英雄 {len(hero)} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
