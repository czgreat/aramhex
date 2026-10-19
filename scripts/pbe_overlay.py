"""过渡数据：现有统计（如 16.20）+ 测试服新海克斯（只给自己用的测试版内置，不发布）。

    python scripts/pbe_overlay.py --raw <测试服原始对局目录> --build 16.21.828 --out <输出目录>
        [--base lolhex/data/bundled] [--kiwi kiwi_augments.json]

- 老海克斯：原样用 --base 的统计（含分段）。
- 被删的海克斯：在 --base 里按出场比例，测试服应该出现至少 REMOVED_MIN_EXPECTED 次、实际一次都没有的；
  另外给了 --game-data（CommunityDragon 测试服 kiwi.bin.json）时，以游戏数据为准：标了停用（Enabled=false）的一律删除；
  测试服里还在出现的会打印出来，人工核对（用户 10-19 定：以游戏数据为准，结合官方公告和实际对局）。
  从所有英雄的海克斯表里去掉（英雄详情页不再列出，同稀有度排名的分母也跟着变），记在 meta.json 的 removed_augments。
- 新海克斯不和老海克斯一起排名（recommend.py），只显示测试服胜率。
- 新海克斯（测试服里出现、--base 里没有的）：补进每个英雄的海克斯表，局数、胜场照实写；
  meta.json 的 provisional_augments 记它们在测试服所有英雄上的合计（推荐按这个打底，见 recommend.py）。
  测试服不分段，分段表里补的是同一份。
- 测试服对局按 --base 同样的有效局规则过滤：时长 ≥240 秒、10 人齐、5 胜 5 负、正常结束。
- latest.json 重算校验值，样本时间写成生成时刻：比下载的旧数据新，客户端会选它；正式数据发布后（样本时间更新）自动换掉。
原始对局是私有数据，只在本机读；输出只有按英雄、海克斯汇总的局数和胜场。
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import gzip
import hashlib
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TZ = dt.timezone(dt.timedelta(hours=8))
RARITY = {"kSilver": "silver", "kGold": "gold", "kPrismatic": "prismatic"}
REMOVED_MIN_EXPECTED = 30


def valid(g: dict, build: str) -> bool:
    ps = g.get("participants") or []
    return (g.get("queueId") == 2400 and str(g.get("gameVersion", "")).startswith(build + ".")
            and g.get("gameDuration", 0) >= 240 and g.get("endOfGameResult") == "GameComplete"
            and len(ps) == 10 and sum(1 for p in ps if p.get("win")) == 5)


def load_games(raw: Path, build: str) -> list[dict]:
    games, seen = [], set()
    for f in sorted(glob.glob(str(raw / "*.ndjson.gz"))):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                g = json.loads(line)["body"]["json"]
                if g["gameId"] not in seen and valid(g, build):
                    seen.add(g["gameId"])
                    games.append(g)
    return games


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="过渡数据：现有统计 + 测试服新海克斯")
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--build", required=True, help="测试服构建号前缀，如 16.21.828")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--base", type=Path, default=ROOT / "lolhex" / "data" / "bundled")
    ap.add_argument("--kiwi", type=Path, help="腾讯 kiwi_augments.json（补新海克斯的名字和稀有度）")
    ap.add_argument("--game-data", type=Path, help="CommunityDragon 测试服 kiwi.bin.json（看哪些海克斯停用了）")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    base_manifest = json.loads((a.base / "latest.json").read_text(encoding="utf-8"))
    meta = json.loads((a.base / "meta.json").read_text(encoding="utf-8"))
    base_ha = json.loads((a.base / "hero_augments.json").read_text(encoding="utf-8"))
    known = {aid for augs in base_ha.values() for aid in augs} | set((meta.get("excluded_augments") or {}))

    games = load_games(a.raw, a.build)
    if not games:
        print("没有符合条件的测试服对局", file=sys.stderr)
        return 1
    per_hero: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    total: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    seen: set[str] = set()
    seen_n: dict[str, int] = defaultdict(int)
    rows = 0
    for g in games:
        for p in g["participants"]:
            rows += 1
            w = 1 if p.get("win") else 0
            augs = {str(p.get(f"playerAugment{i}")) for i in range(1, 7) if p.get(f"playerAugment{i}")}
            seen |= augs
            for aid in augs:
                seen_n[aid] += 1
            for aid in augs - known:
                s = per_hero[str(p["championId"])][aid]
                s[0] += 1
                s[1] += w
                total[aid][0] += 1
                total[aid][1] += w

    base_games: dict[str, int] = defaultdict(int)
    for augs in base_ha.values():
        for aid, v in augs.items():
            base_games[aid] += v["games"]
    base_rows = sum(json.loads((a.base / "heroes.json").read_text(encoding="utf-8"))[h]["games"]
                    for h in json.loads((a.base / "heroes.json").read_text(encoding="utf-8")))
    removed = {aid: n for aid, n in base_games.items()
               if aid not in seen and n / max(1, base_rows) * rows >= REMOVED_MIN_EXPECTED}
    disabled: set[str] = set()
    if a.game_data:
        gd = json.loads(a.game_data.read_text(encoding="utf-8"))
        disabled = {str(v["AugmentPlatformId"]) for v in gd.values()
                    if isinstance(v, dict) and v.get("__type") == "AugmentData" and v.get("AugmentPlatformId")
                    and v.get("Enabled") in (False, 0)}
        for aid in sorted(disabled):
            if aid in base_games:
                removed[aid] = base_games[aid]
            if seen_n.get(aid):
                print(f"核对：{aid} 游戏数据标了停用，但测试服里出现 {seen_n[aid]} 次（按停用删除）")

    out = a.out
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(a.base, out)

    def merge(rel: str) -> None:
        ha = json.loads((out / rel).read_text(encoding="utf-8"))
        for row in ha.values():
            for aid in removed:
                row.pop(aid, None)
        for hid, augs in per_hero.items():
            row = ha.setdefault(hid, {})
            for aid, (n, w) in augs.items():
                row[aid] = {"games": n, "wins": w, "win_rate": round(w / n, 6), "low_sample": True}
        _dump(out / rel, ha)

    for rel in base_manifest["files"]:
        if rel.endswith("hero_augments.json"):
            merge(rel)

    first = min(g["gameCreation"] for g in games)
    last = max(g["gameCreation"] for g in games)
    span = (f"{dt.datetime.fromtimestamp(first / 1000, TZ):%m-%d %H:%M} – "
            f"{dt.datetime.fromtimestamp(last / 1000, TZ):%m-%d %H:%M}")
    source = f"测试服 {a.build}，{span}，{len(games)} 局"
    meta["provisional_augments"] = {aid: {"games": n, "wins": w, "win_rate": round(w / n, 6), "source": source}
                                    for aid, (n, w) in sorted(total.items())}
    def why(aid: str, n: int) -> str:
        if aid in disabled:
            return "测试服游戏数据标了停用" + (f"（测试服里还出现 {seen_n[aid]} 次）" if seen_n.get(aid) else "")
        return (f"{source}里一次都没出现（按 {base_manifest.get('patch')} 的出场比例应出现约 "
                f"{round(n / max(1, base_rows) * rows)} 次），判断已删除")
    meta["removed_augments"] = {aid: {"base_games": n, "reason": why(aid, n)} for aid, n in sorted(removed.items())}
    meta["provisional_note"] = (f"过渡数据：老海克斯用 {base_manifest.get('patch')} 正式服统计；"
                                f"新海克斯用{source}（样本少，只作参考）")
    _dump(out / "meta.json", meta)

    if a.kiwi and (out / "augments.json").exists():
        names = json.loads((out / "augments.json").read_text(encoding="utf-8"))
        for k in json.loads(a.kiwi.read_text(encoding="utf-8")):
            aid = str(k["augmentID"])
            if aid in total and aid not in names:
                names[aid] = {"name": (k.get("name_cn") or aid).strip(), "rarity": RARITY.get(k.get("level"), "unknown")}
        _dump(out / "augments.json", names)

    manifest = dict(base_manifest)
    manifest["snapshot_at"] = dt.datetime.now(TZ).isoformat(timespec="seconds")
    manifest["provisional"] = {"source": source, "augments": len(total), "removed": len(removed), "base_patch": base_manifest.get("patch"),
                               "base_snapshot_at": base_manifest.get("snapshot_at")}
    manifest["files"] = {rel: {"sha256": _sha(out / rel), "size": (out / rel).stat().st_size}
                         for rel in base_manifest["files"]}
    _dump(out / "latest.json", manifest)
    print(f"{source}；新海克斯 {len(total)} 个，补进 {len(per_hero)} 个英雄；判断已删除 {len(removed)} 个；输出 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
