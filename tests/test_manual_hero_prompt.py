"""中途启动的提示、确认选择和下一局状态。"""

import pytest


@pytest.mark.parametrize("manual", [False, True])
def test_missing_hero_prompt_and_next_game(store, tmp_path, monkeypatch, manual):
    from PySide6.QtWidgets import QApplication
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui.overlay import ControlPanel
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Engine, "_prefetch_hero", lambda *a: None)
    engine = Engine(Settings(), home=tmp_path, store=store)
    panel = ControlPanel(engine)
    monkeypatch.setattr(panel, "_place_body", lambda: None)
    monkeypatch.setattr(panel, "give_back_focus", lambda: None)
    engine._set(phase="ingame")
    panel.update_from(engine.snapshot())
    assert panel.expanded
    assert "未识别到英雄，请手动选择" in panel.hero_label.text()
    panel.toggle()
    panel.update_from(engine.snapshot())
    assert not panel.expanded  # 用户收起后不反复打扰。
    if manual:
        panel.search.setText("提莫")
        panel.confirm_hero.click()
        assert engine.state["hero_id"] == "17"
        assert engine._manual_hero
        panel.search.setText("不存在的英雄名称")
        panel.confirm_hero.click()
        assert engine.state["hero_id"] == "17"
        assert not panel.search_feedback.isHidden()
    engine._on_game_end()
    engine._set(phase="client")
    panel.update_from(engine.snapshot())
    assert engine.state["hero_id"] is None
    assert not engine._manual_hero
    engine._apply_champ_select({"mine": "1", "team": [], "bench": []})
    assert engine.state["hero_id"] == "1"
    engine._set(phase="ingame")
    panel.update_from(engine.snapshot())
    assert not panel._missing_hero_notified
    engine._on_game_end()
    engine._set(phase="client")
    panel.update_from(engine.snapshot())
    engine._set(phase="ingame")
    panel.expanded = False
    panel.update_from(engine.snapshot())
    assert panel.expanded  # 下一局依然未知时重新提示。
    panel.close()
    panel.body_win.close()
    app.processEvents()


def test_missing_hero_panel_stays_open_until_hero_chosen(store, tmp_path, monkeypatch):
    """进游戏没认出英雄自动展开的浮窗：选好英雄前不按 5 秒收起；选好后照常收起。"""
    import time
    from PySide6.QtWidgets import QApplication
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui import overlay
    from lolhex.ui.overlay import ControlPanel
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Engine, "_prefetch_hero", lambda *a: None)
    engine = Engine(Settings(), home=tmp_path, store=store)
    panel = ControlPanel(engine)
    monkeypatch.setattr(panel, "_place_body", lambda: None)
    monkeypatch.setattr(panel, "give_back_focus", lambda: None)
    monkeypatch.setattr(panel, "isVisible", lambda: True)
    monkeypatch.setattr(panel, "_typing", lambda: False)
    monkeypatch.setattr(overlay.QCursor, "pos", staticmethod(lambda: overlay.QPoint(-9999, -9999)))
    engine._set(phase="ingame")
    panel.update_from(engine.snapshot())
    assert panel.expanded and panel._hold_for_hero
    panel._away_since = time.monotonic() - 60
    panel.auto_hide("ingame", False)
    assert panel.expanded  # 还没选英雄：不收
    engine.set_hero("17")
    panel.update_from(engine.snapshot())
    assert not panel._hold_for_hero
    panel.auto_hide("ingame", False)
    panel._away_since = time.monotonic() - 60
    panel.auto_hide("ingame", False)
    assert not panel.expanded  # 选好了：照常收起
    panel.close()
    panel.body_win.close()
    app.processEvents()


def test_missing_hero_panel_manual_collapse_releases_hold(store, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui.overlay import ControlPanel
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Engine, "_prefetch_hero", lambda *a: None)
    engine = Engine(Settings(), home=tmp_path, store=store)
    panel = ControlPanel(engine)
    monkeypatch.setattr(panel, "_place_body", lambda: None)
    engine._set(phase="ingame")
    panel.update_from(engine.snapshot())
    assert panel._hold_for_hero
    panel.toggle()  # 玩家点图标收起
    assert not panel.expanded and not panel._hold_for_hero
    panel.update_from(engine.snapshot())
    assert not panel.expanded  # 不再弹
    panel.close()
    panel.body_win.close()
    app.processEvents()


@pytest.mark.parametrize("hero_id", [None, "17"])
def test_badge_says_when_hero_unknown(hero_id):
    """没认出英雄时三选一标签上写明按全英雄数据，认出了就不写。"""
    from PySide6.QtWidgets import QApplication
    from lolhex.ui.overlay import BadgeLayer
    from lolhex.vision import window
    app = QApplication.instance() or QApplication([])
    layer = BadgeLayer()
    snap = {"settings": {}, "offer": {"boxes": [[700, 430, 1000, 470]]},
            "evaluation": {"hero_id": hero_id, "slots": [{"index": 0, "verdict": "best", "score": 2}]}}
    layer.update_from(snap, window.Rect(0, 0, 1920, 1080))
    lines = [ln for item in layer._badge_layout(snap) for ln in item[3]]
    assert ("未识别英雄 · 按全英雄数据" in lines) == (hero_id is None)
    layer.close()
    app.processEvents()
