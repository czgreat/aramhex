"""用选人状态和离屏浮层验证阵容汇总。"""

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage

from lolhex.ui import overlay
from lolhex.vision import window as win
from lolhex.vision.champselect import boxes


def test_team_rating_updates_on_hero_swap(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(win, "_drawn", {})
    layer = overlay.ChampSelectLayer()
    monkeypatch.setattr(layer, "_place", lambda *args: None)
    monkeypatch.setattr(layer, "_scale", lambda: 1.0)
    actual_paint = layer._paint_team_rating
    calls = []

    def record(painter, rate, grade):
        rect = actual_paint(painter, rate, grade)
        calls.append((rate, grade, rect))
        return rect

    monkeypatch.setattr(layer, "_paint_team_rating", record)
    size = (960, 540)
    client = win.Rect(0, 0, *size)
    layer.resize(*size)
    team = [{"hero_id": str(i), "win": .52, "rank": 28, "total": 100, "box": box}
            for i, box in enumerate(boxes("team", *size))]
    team[4] = {**team[4], "win": .49, "rank": 48}
    bench = [{"hero_id": "6", "win": .65, "rank": 1, "total": 100, "box": boxes("bench", *size)[0]}]
    snap = {"champ_select": {"team": team, "mine": "0", "bench": bench}}

    def draw():
        layer.update_from(snap, client)
        img = QImage(*size, QImage.Format_ARGB32)
        img.fill(0)
        layer.render(img, QPoint())
        return img

    draw()
    rate, grade, _ = calls[-1]
    assert rate == pytest.approx(.514)
    assert grade == "B"

    team = [*team[:4], {**team[4], "hero_id": "5", "win": .55, "rank": 12}]
    snap["champ_select"] = {**snap["champ_select"], "team": team}
    img = draw()
    rate, grade, rect = calls[-1]
    assert rate == pytest.approx(.526)
    assert grade == "A"
    assert img.pixelColor(rect.center()).alpha() > 0
    assert rect.bottom() < team[0]["box"][1]
    assert rect.right() < size[0]
    assert any(r.top <= rect.top() and r.left <= rect.left() and r.right >= rect.right()
               and r.bottom >= rect.bottom() for r in win.drawn_rects())

    snap["settings"] = {"show_winrate": False}
    draw()
    assert calls[-1][2].width() < rect.width()

    for incomplete in (team[:-1], [*team[:4], {**team[4], "win": None}]):
        snap["champ_select"] = {**snap["champ_select"], "team": incomplete}
        calls.clear()
        draw()
        assert not calls
    layer.hide()
    app.processEvents()
