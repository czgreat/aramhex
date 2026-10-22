"""选英雄头像识别用的腾讯方形头像，打成一个文件随程序内置。

    python scripts/update_hero_icons.py [--out lolhex/data/hero_icons.json]

以前首次运行才从腾讯 CDN 逐个下载（全局限速 0.35 秒一次，173 张约 1 分钟），这一分钟里进选人认不出自己的英雄。
内置后启动即可用；腾讯原图字节不改（和下载的缓存一模一样），按英雄 alias 存。之后新出的英雄照常补下载。
拳头客户端那一套另见 scripts/update_client_icons.py。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lolhex.data import tencent  # noqa: E402


def fetch(url: str) -> bytes:
    r = requests.get(url, timeout=60, headers={"Referer": tencent.REFERER})
    r.raise_for_status()
    return r.content


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "lolhex" / "data" / "hero_icons.json"))
    args = ap.parse_args()
    heroes = tencent.parse_hero_list(tencent.fetch_hero_list())
    todo = sorted((h["alias"], h["icon"]) for h in heroes.values() if h.get("alias"))
    with ThreadPoolExecutor(4) as ex:
        blobs = list(ex.map(lambda t: fetch(t[1]), todo))
    icons = {alias: base64.b64encode(b).decode("ascii") for (alias, _), b in zip(todo, blobs)}
    out = {"source": f"{tencent.GTIMG}/champion/{{alias}}.png", "fetched": time.strftime("%Y-%m-%d"), "icons": icons}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"{len(icons)} 个英雄头像 → {args.out}（{Path(args.out).stat().st_size / 1e6:.1f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
