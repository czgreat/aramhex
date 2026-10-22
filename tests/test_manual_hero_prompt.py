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
