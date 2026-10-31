"""三选一刚弹出、某张卡字没读全：前 2 秒写「识别中…」，还认不出才写「未知」（1.1.4）。"""
import pytest

from lolhex.config import Settings
from lolhex.engine import Engine
from lolhex.vision.detector import Offer
from lolhex.vision.window import Rect

BOXES = [(440, 420, 750, 470), (810, 420, 1120, 470), (1180, 420, 1490, 470)]


def labels(layer, snap):
    return [label for _, label, *_ in layer._badge_layout(snap)]


def test_unread_card_shows_reading_then_unknown(store, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from lolhex.ui import overlay
    import lolhex.engine as engine_module

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(overlay.win, "IS_WINDOWS", False)
    clock = [100.0]
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(overlay.time, "monotonic", lambda: clock[0])
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase="ingame", hero_id="17")
    layer = overlay.BadgeLayer()
    rect = Rect(0, 0, 1920, 1080)

    engine._on_offer(Offer(["2128", None, "1373"], [], [], BOXES))
    snap = engine.snapshot()
    assert snap["offer"]["unread_since"] == [None, 100.0, None]
    layer.update_from(snap, rect)
    assert labels(layer, snap)[1] == "识别中…"

    clock[0] += 1.0  # 别的列刷新了，这一列还是没认出：从第一次没认出算起
    engine._on_offer(Offer(["1029", None, "1373"], [], [], BOXES))
    snap = engine.snapshot()
    assert snap["offer"]["unread_since"][1] == 100.0
    clock[0] += 1.5
    assert labels(layer, snap)[1] == "未知"

    engine._on_offer(Offer(["1029", "2096", "1373"], [], [], BOXES))
    assert engine.snapshot()["offer"]["unread_since"] == [None, None, None]
