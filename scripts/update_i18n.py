"""外服用的名字表：海克斯名、英雄名和选英雄界面上的字，英文、日文、繁中（外加简中用来核对）。

    python scripts/update_i18n.py [--out lolhex/data/i18n.json]

来源都是拳头公开数据：
- 海克斯：CommunityDragon 的 kiwi.bin.json（AugmentPlatformId 就是腾讯的海克斯 ID）+ 各语言 lol.stringtable.json。
  正式服（latest）和测试服（pbe）都取，正式服优先；测试服补上还没上线的新海克斯。
  stringtable 里新加的键是哈希：小写键名的 xxh3_64 取低 38 位，写成 {%010x}（要装 xxhash，只有这个脚本用）。
- 英雄：Data Dragon 各语言 champion.json 的 name 和 title（按 key 即英雄数字 ID）。
- 选英雄界面：rcp-fe-lol-champ-select 的 trans.json（预选栏左边的「可用」、开局选卡的「选择你的英雄」）。
拳头的简中名字和腾讯的应当一致，不一致的条数会打印出来。
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


def augments(branch: str) -> dict[str, dict[str, str]]:
    kiwi = get_json(f"{CD}/{branch}/game/maps/modespecificdata/kiwi.bin.json")
    tra = {str(v["AugmentPlatformId"]): v["NameTra"] for v in kiwi.values()
           if isinstance(v, dict) and v.get("__type") == "AugmentData" and v.get("AugmentPlatformId") and v.get("NameTra")}
    out: dict[str, dict[str, str]] = {}
    for lang, d in LANGS.items():
        table = get_json(f"{CD}/{branch}/game/{d}/data/menu/en_us/lol.stringtable.json")["entries"]
        for aid, key in tra.items():
            name = lookup(table, key)
            if name and name.strip():
                out.setdefault(aid, {})[lang] = name.strip()
    print(f"{branch}: {len(tra)} 个海克斯，有名字的 {len(out)} 个")
    return out


def drop_my_duplicates(aug: dict[str, dict[str, str]], live: set[str]) -> list[tuple]:
    """zh_MY 里两个海克斯同名（2076「保持坚定」和已下线的 1327「坚若磐石」都叫「坚定不移」）时，
    只给内置统计里还在玩的那个留 zh_MY 名字；分不出来就都不留。返回去掉的 [(名字, [ID...], 留下的 ID)]。"""
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
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    aug = augments("pbe")
    aug.update(augments("latest"))  # 正式服的名字优先
    live = set(json.loads((ROOT / "lolhex/data/bundled/augments.json").read_text(encoding="utf-8")))
    print("zh_MY 同名处理（名字, ID, 留给）：", drop_my_duplicates(aug, live))
    ver, hero = heroes()
    ui = ui_texts()

    tx = (json.loads(a.tencent.read_text(encoding="utf-8")) if a.tencent else
          get_json("https://game.gtimg.cn/images/lol/act/img/js/kiwi/kiwi_augments.json"))
    tx = {str(x["augmentID"]): x["name_cn"] for x in tx}
    diff = [(i, tx[i], v["zh_CN"]) for i, v in aug.items() if i in tx and v.get("zh_CN") and v["zh_CN"] != tx[i]]
    print(f"简中名字和腾讯一致 {sum(1 for i, v in aug.items() if tx.get(i) == v.get('zh_CN'))} 个，不一致 {len(diff)} 个", diff[:10])
    print("选英雄界面：", json.dumps(ui, ensure_ascii=False))

    out = {"_说明": "外服名字表，scripts/update_i18n.py 生成。augments/heroes 按腾讯同一套 ID；heroes 每种语言是 [名字, 称号]。",
           "ddragon": ver, "augments": dict(sorted(aug.items(), key=lambda kv: int(kv[0]))),
           "heroes": dict(sorted(hero.items(), key=lambda kv: int(kv[0]))), "ui": ui}
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=0, sort_keys=False) + "\n", encoding="utf-8")
    print(f"写入 {a.out}：海克斯 {len(aug)} 个、英雄 {len(hero)} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
