"""腾讯官方资料打包随程序内置（1.1.2）：连不上腾讯 CDN 时先用这份，联网成功再换成最新的。

    python scripts/update_static.py

生成三个文件（都在 lolhex/data/）：
  static_bundled.json  英雄、海克斯、装备、召唤师技能、版本（data/store.py）
  hero_bundled.json    每个英雄的官方出装、加点、海克斯层级（data/store.py official_hero）
  item_icons.json      装备、召唤师技能小图标，PNG 原样 base64（data/icons.py）

第一次运行时连不上腾讯（加速器、代理、网络拦截），以前英雄和海克斯名单都是空的，选英雄、三选一全都认不出（玩家反馈 10-23）。
抓取和解析与软件里完全一样（DataStore）。英雄头像另见 scripts/update_hero_icons.py。
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lolhex.data import tencent  # noqa: E402
from lolhex.data.icons import BUNDLED as ICONS_OUT  # noqa: E402
from lolhex.data.store import BUNDLED_HEROES as HEROES_OUT, BUNDLED_STATIC as STATIC_OUT, DataStore  # noqa: E402

KEYS = ("patch", "versions", "heroes", "augments", "items", "spells")
MIN_HEROES, MIN_AUGMENTS = 150, 200  # 少于这些多半是腾讯接口出错，不覆盖已提交的那份


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8", newline="\n")
    print(f"  → {path.name}（{path.stat().st_size / 1e6:.2f} MB）")


def _png(url: str) -> bytes | None:
    try:
        r = requests.get("https:" + url if url.startswith("//") else url, timeout=30,
                         headers={"Referer": tencent.REFERER})
        return r.content if r.status_code == 200 and r.content.startswith(b"\x89PNG") else None
    except requests.RequestException:
        return None


def main() -> int:
    today = time.strftime("%Y-%m-%d")
    with tempfile.TemporaryDirectory() as tmp:
        store = DataStore(Path(tmp))
        store._refresh_static()
        static = store.static
        if len(static.get("heroes") or {}) < MIN_HEROES or len(static.get("augments") or {}) < MIN_AUGMENTS:
            print(f"英雄 {len(static.get('heroes') or {})}、海克斯 {len(static.get('augments') or {})}，太少，不写",
                  file=sys.stderr)
            return 1
        out = {k: static.get(k) for k in KEYS}
        out["generated"] = today
        print(f"{out['patch']} 版本：英雄 {len(out['heroes'])}、海克斯 {len(out['augments'])}、装备 {len(out['items'])}、"
              f"召唤师技能 {len(out['spells'])}")
        _dump(STATIC_OUT, out)

        # 单英雄：和软件里一样走 official_hero（腾讯接口全局限速，173 个约两分钟）
        heroes, missing = {}, []
        for hid in sorted(static["heroes"], key=int):
            d = store.official_hero(hid)
            if d and not d.get("bundled"):
                heroes[hid] = {k: v for k, v in d.items() if k != "fetched_at"}
            else:
                missing.append(hid)
        if len(heroes) < MIN_HEROES:
            print(f"单英雄只取到 {len(heroes)} 个，不写 {HEROES_OUT.name}", file=sys.stderr)
            return 1
        print(f"单英雄官方数据 {len(heroes)} 个" + (f"，没取到：{missing}" if missing else ""))
        _dump(HEROES_OUT, {"patch": out["patch"], "generated": today, "heroes": heroes})

    todo = [("item", k, v["icon"]) for k, v in sorted(out["items"].items()) if v.get("icon")]
    todo += [("spell", k, v["icon"]) for k, v in sorted(out["spells"].items()) if v.get("icon")]
    with ThreadPoolExecutor(4) as ex:
        blobs = list(ex.map(lambda t: _png(t[2]), todo))
    icons: dict[str, dict[str, str]] = {}
    for (kind, key, _), b in zip(todo, blobs):
        if b:
            icons.setdefault(kind, {})[key] = base64.b64encode(b).decode("ascii")
    print(f"图标：装备 {len(icons.get('item', {}))}/{sum(t[0] == 'item' for t in todo)}、"
          f"召唤师技能 {len(icons.get('spell', {}))}/{sum(t[0] == 'spell' for t in todo)}")
    _dump(ICONS_OUT, {"generated": today, "icons": icons})
    return 0


if __name__ == "__main__":
    sys.exit(main())
