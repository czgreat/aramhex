import gzip
import json
import sys
from pathlib import Path

from conftest import make_dataset

from lolhex.data.public import Dataset

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import pbe_overlay  # noqa: E402


def _game(gid, build="16.21.828.4168", dur=900, augs=("3086",), win_team=100):
    ps = []
    for i in range(10):
        team = 100 if i < 5 else 200
        p = {"championId": 17 if i == 0 else 1, "teamId": team, "win": team == win_team}
        for j, a in enumerate(augs if i in (0, 5) else ("1029",)):
            p[f"playerAugment{j + 1}"] = int(a)
        ps.append(p)
    return {"gameId": gid, "queueId": 2400, "gameVersion": build, "gameDuration": dur, "endOfGameResult": "GameComplete",
            "gameCreation": 1792300000000 + gid, "participants": ps}


def test_overlay_adds_new_augments_only(tmp_path):
    base = tmp_path / "base"
    make_dataset(base)
    raw = tmp_path / "raw"
    raw.mkdir()
    games = [_game(1), _game(2, win_team=200), _game(3, augs=("3086", "3086")),
             _game(4, build="16.21.826.2490"), _game(5, dur=120), _game(1)]  # 旧构建、太短、重复都不算
    with gzip.open(raw / "a.ndjson.gz", "wt", encoding="utf-8") as fh:
        for g in games:
            fh.write(json.dumps({"platform": "PBE", "id": g["gameId"], "body": {"json": g}}) + "\n")
    out = tmp_path / "out"
    assert pbe_overlay.main(["--raw", str(raw), "--build", "16.21.828", "--out", str(out), "--base", str(base)]) == 0
    ds = Dataset(out, "bundled")  # 校验值重算过，能正常读
    heroes, ha = ds.table(None)
    # 提莫（0 号位，100 队）3 局：赢、输、赢；同一局里重复拿只算一次
    assert ha["17"]["3086"] == {"games": 3, "wins": 2, "win_rate": round(2 / 3, 6), "low_sample": True}
    assert ha["17"]["1029"]["games"] == 40000  # 老海克斯原样
    assert ds.table("mid")[1]["17"]["3086"]["games"] == 3  # 分段表也补上
    pv = ds.meta["provisional_augments"]
    assert list(pv) == ["3086"] and pv["3086"]["games"] == 6 and pv["3086"]["wins"] == 3
    assert ds.snapshot_at > "2026-10-13" and ds.manifest["provisional"]["augments"] == 1


def test_overlay_drops_augments_missing_from_pbe(tmp_path, monkeypatch):
    # 2128 在底子里占 30000/700000，测试服 30 人次里按比例应出现约 1.3 次，一次没有 → 判断已删除；
    # 1373 应出现约 0.9 次，不到门槛，留着
    monkeypatch.setattr(pbe_overlay, "REMOVED_MIN_EXPECTED", 1)
    base = tmp_path / "base"
    make_dataset(base)
    raw = tmp_path / "raw"
    raw.mkdir()
    with gzip.open(raw / "a.ndjson.gz", "wt", encoding="utf-8") as fh:
        for g in (_game(1), _game(2), _game(3)):
            fh.write(json.dumps({"platform": "PBE", "id": g["gameId"], "body": {"json": g}}) + "\n")
    out = tmp_path / "out"
    assert pbe_overlay.main(["--raw", str(raw), "--build", "16.21.828", "--out", str(out), "--base", str(base)]) == 0
    ds = Dataset(out, "bundled")
    assert "2128" not in ds.table(None)[1]["17"] and "2128" not in ds.table("top")[1].get("17", {})
    assert "1373" in ds.table(None)[1]["17"] and "1029" in ds.table(None)[1]["17"]
    assert list(ds.meta["removed_augments"]) == ["2128"] and ds.manifest["provisional"]["removed"] == 1
