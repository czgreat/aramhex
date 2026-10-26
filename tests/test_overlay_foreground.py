"""Exercise the actual overlay tick and badge visibility across AltTab."""
from types import SimpleNamespace


def test_game_badges_hide_in_background_and_return_without_rescan(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from lolhex.ui.overlay import Overlay, BadgeLayer
    from lolhex.vision import window
    app=QApplication.instance() or QApplication([])
    rect=window.Rect(-640,120,1920,1080)
    front=[True]
    monkeypatch.setattr(window,"find_game_window",lambda:1)
    monkeypatch.setattr(window,"client_rect",lambda _:rect)
    monkeypatch.setattr(window,"is_foreground",lambda _:front[0])
    snap={"phase":"ingame","offer":{"boxes":[]},"evaluation":{"slots":[]}}
    ov=Overlay.__new__(Overlay)
    ov.engine=SimpleNamespace(snapshot=lambda:snap)
    ov.badges=BadgeLayer()
    ov.champ=SimpleNamespace(update_from=lambda *args:None)
    ov.autoshot=SimpleNamespace(tick=lambda *args:None)
    ov.panel=SimpleNamespace(update_from=lambda *args:None,auto_hide=lambda *args:None,
        isVisible=lambda:True,place=lambda *args,**kwargs:None)
    try:
        ov._tick()
        assert ov.badges.isVisible()
        front[0]=False
        ov._tick()
        assert not ov.badges.isVisible()
        assert snap["offer"] is not None
        front[0]=True
        ov._tick()
        assert ov.badges.isVisible()
    finally:
        ov.badges.close()
        app.processEvents()
