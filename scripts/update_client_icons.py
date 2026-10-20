"""选英雄界面预选栏用的英雄头像（拳头客户端那一套），打成一个文件随程序内置。

    python scripts/update_client_icons.py [--out lolhex/data/client_icons.json]

头像识别本来只用腾讯 CDN 的方形头像（game.gtimg.cn/.../champion/{alias}.png）。拳头这两年给一些英雄换了头像
（萨科、乌迪尔、阿狸、赫卡里姆等），客户端预选栏显示新的，腾讯 CDN 还是旧的，比对分数不够就认不出。
这里取 CommunityDragon 的 champion-icons（就是客户端用的图），缩到 64×64 存成 JPEG，按英雄数字 ID 放进一个 JSON，
程序把它当每个英雄的第二张参考图。打成一个文件：增量更新时只多一个文件；玩家电脑也不用连国外网站。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import requests

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1"
SIDE = 64


def fetch(url: str) -> bytes:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "lolhex" / "data" / "client_icons.json"))
    args = ap.parse_args()
    # 60001 起是别的模式专用的英雄（Jade_Annie 等），不要
    ids = [str(c["id"]) for c in json.loads(fetch(f"{BASE}/champion-summary.json")) if 0 < c["id"] < 10000]

    def one(hid: str) -> tuple[str, str | None]:
        try:
            img = cv2.imdecode(np.frombuffer(fetch(f"{BASE}/champion-icons/{hid}.png"), np.uint8), cv2.IMREAD_COLOR)
        except requests.RequestException as e:
            print(f"下载失败 {hid}：{e}", file=sys.stderr)
            return hid, None
        img = cv2.resize(img, (SIDE, SIDE), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        return hid, base64.b64encode(buf.tobytes()).decode("ascii") if ok else None

    with ThreadPoolExecutor(8) as ex:
        icons = {hid: b for hid, b in ex.map(one, ids) if b}
    missing = len(ids) - len(icons)
    out = {"source": f"{BASE}/champion-icons", "fetched": time.strftime("%Y-%m-%d"), "side": SIDE,
           "icons": dict(sorted(icons.items(), key=lambda kv: int(kv[0])))}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(icons)} 个英雄头像 → {args.out}（{Path(args.out).stat().st_size // 1024} KB）" + (f"，{missing} 个没下到" if missing else ""))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
