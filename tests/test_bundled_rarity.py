"""内置统计数据里的稀有度要和软件的更正表一致（腾讯资料标错的，如 2078 连锁反应标成棱彩）。"""
import json
from pathlib import Path

from lolhex.data import tencent

BUNDLED = Path(tencent.__file__).resolve().parent / "bundled"


def test_bundled_stats_rarity_matches_fixes():
    files = sorted(BUNDLED.rglob("augments.json"))
    assert files
    for p in files:
        rows = json.loads(p.read_text(encoding="utf-8"))
        for aid, rarity in tencent.RARITY_FIX.items():
            if aid in rows:
                assert rows[aid]["rarity"] == rarity, (p.relative_to(BUNDLED).as_posix(), aid, rows[aid])
