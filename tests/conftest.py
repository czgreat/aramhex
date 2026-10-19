"""测试夹具：腾讯官方资料用 2026-09-25 实抓并裁剪过的真实数据；统计数据用一份小的合成数据，离线构建 PublicStore。"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

BANDS = ("casual", "mid", "high", "top")


def fixture(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _hero(games, wins, rank=1):
    return {"name": None, "title": None, "games": games, "wins": wins, "win_rate": round(wins / games, 6),
            "pick_rate": 0.01, "rank": rank}


def _aug(games, win):
    wins = round(games * win)
    return {"games": games, "wins": wins, "win_rate": round(wins / games, 6), "low_sample": games < 3000}


# 提莫（17）：腾讯把炽燃利息（2128）排黄金第 1，但带虚幻武器（1029）的胜率明显更高
TEEMO = {"2128": _aug(30000, 0.52), "1029": _aug(40000, 0.60), "1373": _aug(20000, 0.55)}


def make_dataset(folder: Path, snapshot_at="2026-10-13T11:56:47+08:00", patch="16.20", bands=True,
                 heroes=None, hero_augments=None, seq=0) -> dict:
    """在 folder 写一份公开数据（和数据仓库同格式），返回清单。"""
    heroes = heroes or {"17": _hero(400000, 212000, 1), "1": _hero(300000, 147000, 2)}
    hero_augments = hero_augments or {"17": dict(TEEMO), "1": {"1029": _aug(5000, 0.47)}}
    files = {"heroes.json": heroes, "hero_augments.json": hero_augments,
             "meta.json": {"schema_version": 1, "patch": patch, "mode": "normal",
                           "matches": {"collected": 72000, "valid": 70000, "excluded": 2000},
                           "window": {"start": "2026-10-09T08:14:00+08:00", "end_exclusive": "2026-10-13T10:00:00+08:00"},
                           "regions": ["艾欧尼亚"]}}
    if bands:
        for i, b in enumerate(BANDS):  # 分段越高，提莫越强，便于断言
            files[f"bands/{b}/heroes.json"] = {"17": _hero(1000 * (4 - i), int(1000 * (4 - i) * (0.50 + 0.02 * i))),
                                               "1": _hero(1000, 500)}
            files[f"bands/{b}/hero_augments.json"] = {"17": {"1029": _aug(500, 0.50 + 0.03 * i)}}
    manifest = {"schema_version": 1, "patch": patch, "path": f"patches/{patch}", "snapshot_at": snapshot_at,
                "window": files["meta.json"]["window"], "matches": 70000, "bands": bands, "files": {}, "seq": seq}
    for name, obj in files.items():
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        p = folder / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        manifest["files"][name] = {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
    (folder / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest


@pytest.fixture
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("测试中不应联网")
    monkeypatch.setattr("lolhex.data.http.get", boom)
    monkeypatch.setattr("lolhex.data.http.head_ok", boom)
    monkeypatch.setattr("requests.get", boom)


@pytest.fixture
def store(tmp_path, no_network):
    """离线 PublicStore：官方静态资料、提莫的单英雄官方数据、一份内置统计数据都已就绪，且禁止联网。"""
    from lolhex.data import tencent
    from lolhex.data.public import PublicStore

    now = time.time()
    root = tmp_path / "data"
    root.mkdir()
    static = {
        "fetched_at": now, "patch": "16.20", "versions": [{"name": "16.20"}],
        "heroes": tencent.parse_hero_list(fixture("tencent_hero_list.json")),
        "augments": tencent.parse_kiwi_augments(fixture("tencent_kiwi_augments.json")),
        "items": tencent.parse_items(fixture("tencent_items.json")),
        "spells": tencent.parse_summoner_spells(fixture("tencent_summoner_spells.json")),
    }
    hero17 = tencent.parse_hero_hex(fixture("tencent_hero17.json"))
    hero17.update(spells=tencent.parse_hero_spells(fixture("tencent_hero17_spells.json")),
                  fetched_at=now, patch="16.20", splash=None)
    (root / "static.json").write_text(json.dumps(static, ensure_ascii=False), encoding="utf-8")
    (root / "hero").mkdir()
    (root / "hero" / "17.json").write_text(json.dumps(hero17, ensure_ascii=False), encoding="utf-8")
    bundled = tmp_path / "bundled"
    make_dataset(bundled)
    return PublicStore(root, bundled=bundled, check_updates=False)


@pytest.fixture(autouse=True)
def _no_site(monkeypatch, request):
    """测试不连真的官网镜像；要测镜像的用例自己给地址（标记 site）。"""
    if "site" in request.keywords:
        return
    from lolhex import update

    def offline(*a, **k):
        raise update.requests.ConnectionError("测试不连官网")
    monkeypatch.setattr(update, "check_site", offline)


def pytest_configure(config):
    config.addinivalue_line("markers", "site: 允许连（假的）官网镜像")


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    """每个测试用自己的临时数据目录和空的云控缓存，不读开发机上真的 cloud.json / config.json。
    要固定目录的用例自己再 setenv 覆盖。"""
    from lolhex import cloud
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path / "_home"))
    monkeypatch.setattr(cloud, "_cache", None)
