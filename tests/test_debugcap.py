import json

import numpy as np

from lolhex import debugcap
from lolhex.vision.window import Rect


def test_game_settings_picks_display_keys():
    persisted = {"files": [{"name": "Game.cfg", "sections": [
        {"name": "General", "settings": [{"name": "Width", "value": "2560"}, {"name": "Height", "value": "1440"},
                                         {"name": "WindowMode", "value": "2"}, {"name": "Colors", "value": "32"}]},
        {"name": "HUD", "settings": [{"name": "MinimapScale", "value": "1.3300"},
                                     {"name": "GlobalScale", "value": "0.5"}, {"name": "FlipMiniMap", "value": "0"}]}]}]}
    got = debugcap.game_settings(persisted)
    assert got == {"Width": "2560", "Height": "1440", "WindowMode": "2", "MinimapScale": "1.3300",
                   "GlobalScale": "0.5", "FlipMiniMap": "0"}
    assert debugcap._flipped(got) is False
    assert debugcap._flipped({}) is None


def test_minimap_boxes_cover_the_corner():
    both = debugcap.minimap_boxes(1920, 1080, None)
    assert set(both) == {"left", "right"}
    x, y, w, h = both["right"]
    assert x + w == 1920 and y + h == 1080 and w == 768 and h == 594
    assert set(debugcap.minimap_boxes(1920, 1080, True)) == {"left"}
    assert set(debugcap.minimap_boxes(1920, 1080, False)) == {"right"}


def test_prune_removes_oldest_sessions(tmp_path):
    for name in ("20261018-100000", "20261018-110000", "20261018-120000"):
        d = tmp_path / name / "shots"
        d.mkdir(parents=True)
        (d / "a.jpg").write_bytes(b"x" * 100)
    (tmp_path / "manual").mkdir()
    (tmp_path / "manual" / "m.png").write_bytes(b"x" * 1000)
    keep = tmp_path / "20261018-100000"
    removed = debugcap.prune(tmp_path, max_bytes=150, keep=keep)
    assert [d.name for d in removed] == ["20261018-110000", "20261018-120000"]
    assert keep.is_dir() and (tmp_path / "manual").is_dir()
    assert debugcap.prune(tmp_path / "missing") == []


class _Offer:
    slots = ["1029", None, "2128"]
    texts = ["虚幻武器", "?", "炽燃利息"]
    scores = [98.0, 0.0, 95.5]
    boxes = [(10, 20, 100, 30), None, (300, 20, 100, 30)]


class _Engine:
    def __init__(self, home):
        self.home = home
        self.state = {}


def test_offer_saves_png_minimap_and_json(tmp_path, monkeypatch):
    from lolhex.vision import capture
    frame = np.random.default_rng(0).integers(0, 255, (360, 640, 3), dtype=np.uint8)
    monkeypatch.setattr(capture, "grab", lambda rect: frame)
    rec = debugcap.DebugRecorder(_Engine(tmp_path))
    rec.session = tmp_path / "debug" / "20261018-120000"
    rec.session.mkdir(parents=True)
    rec.offer(Rect(0, 0, 640, 360), _Offer())
    shots = sorted(p.name for p in (rec.session / "shots").iterdir())
    assert len(shots) == 2 and shots[0].endswith(".json") and shots[1].endswith(".png")
    info = json.loads((rec.session / "shots" / shots[0]).read_text(encoding="utf-8"))
    assert info["slots"] == _Offer.slots and info["boxes"][1] is None and info["rect"] == [0, 0, 640, 360]
    assert sorted(p.name.rsplit("-", 1)[1] for p in (rec.session / "minimap").iterdir()) == ["left.png", "right.png"]
    # 选完之后安排两张
    rec.offer_closed()
    assert [tag for _, tag in rec._due] == ["picked", "picked"]
    rec.offer_closed()
    assert len(rec._due) == 2


def test_offer_before_session_only_marks_open(tmp_path):
    rec = debugcap.DebugRecorder(_Engine(tmp_path))
    rec.offer(Rect(0, 0, 640, 360), _Offer())
    assert rec._offer_open and not (tmp_path / "debug").exists()


def test_read_mapskin_from_latest_game_log(tmp_path):
    logs = tmp_path / "Logs" / "GameLogs"
    old, new = logs / "2026-10-18T12-00-00", logs / "2026-10-18T14-20-45"
    for d, skin in ((old, "mapskin_map12_bloom"), (new, "Default")):
        d.mkdir(parents=True)
        (d / f"{d.name}_r3dlog.txt").write_text(
            "000001.792| ALWAYS| GameStartInfo GPP match Id = 1d5f2baa, mapskin = %s\n" % skin, encoding="utf-8")
    import os
    os.utime(old, (1, 1))
    assert debugcap.read_mapskin(tmp_path) == "Default"
    assert debugcap.read_mapskin(tmp_path / "missing") is None


def test_card_lines_keeps_band_cards_only():
    got = debugcap.card_lines(["太空律动 拉克丝", "迪斯科灯球闪烁着最耀眼的光芒！", "不灭狂雷", "113", "双方军团开始出兵了",
                               "K/DA 阿狸"])
    assert got == ["太空律动 拉克丝", "迪斯科灯球闪烁着最耀眼的光芒！", "K/DA 阿狸"]
