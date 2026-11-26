"""对局中按需模式：详情顶上贴着按钮那一侧写一句「收起后按钮隐藏，鼠标移过来浮现」。"""

import logging

import pytest


@pytest.fixture
def panel(store, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui.overlay import ControlPanel
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Engine, "_prefetch_hero", lambda *a: None)
    engine = Engine(Settings(), home=tmp_path, store=store)
    p = ControlPanel(engine)
    monkeypatch.setattr(p, "_place_body", lambda: None)
    monkeypatch.setattr(p, "give_back_focus", lambda: None)
    p.expanded = True
    yield p, engine
    p.close()
    p.body_win.close()
    app.processEvents()


def test_hint_on_hero_name_row(panel):
    p, engine = panel
    engine._set(phase="ingame", hero_id="17")
    p.update_from(engine.snapshot())
    t = p.hero_label.text()
    assert "收起后按钮隐藏，鼠标移过来浮现 ↗" in t and t.startswith("<table")
    assert "提莫" in t.split("</table>")[0]  # 和英雄名同一行
    assert "评级" in t.split("</table>")[1] or "胜率" in t.split("</table>")[1]
    p.corner = "top-left"
    p.update_from(engine.snapshot())
    assert "鼠标移到左上角浮现" in p.hero_label.text()
    p.corner = "top-right"
    head = p._with_hide_hint("烈娜塔 · 戈拉斯克", "炼金男爵", engine.snapshot())  # 太长：不写称号，提示不折行
    assert "烈娜塔" in head and "炼金男爵" not in head
    head = p._with_hide_hint("奥瑞利安索尔", "铸星龙王", engine.snapshot())
    assert "铸星龙王" in head
    p.corner = "top-left"
    engine._set(hero_id=None)  # 没认出英雄：提示照样在同一行
    p.update_from(engine.snapshot())
    assert "未识别到英雄" in p.hero_label.text() and "左上角浮现" in p.hero_label.text()


def test_hint_only_when_button_really_hides(panel):
    p, engine = panel
    engine._set(phase="client", hero_id="17")  # 选英雄：按钮一直在
    p.update_from(engine.snapshot())
    assert "浮现" not in p.hero_label.text() and "<br>" in p.hero_label.text()
    engine.settings.overlay_on_demand = False  # 云控关了按需：按钮一直在
    engine._set(phase="ingame")
    p.update_from(engine.snapshot())
    assert "浮现" not in p.hero_label.text()
    engine.settings.overlay_on_demand = True
    p.update_from(engine.snapshot())
    assert "浮现" in p.hero_label.text()


@pytest.mark.parametrize("busy,in_game,modal,why", [
    (True, True, False, "对局中"), (True, False, False, "选英雄中"), (False, False, True, "已有弹窗开着")])
def test_deferred_cloud_log_says_why(busy, in_game, modal, why, tmp_path, monkeypatch, caplog):
    from PySide6 import QtWidgets
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui import mainwindow, overlay
    eng = Engine(Settings.load())
    ov = overlay.Overlay(eng)
    w = mainwindow.MainWindow(eng, ov)
    try:
        monkeypatch.setattr(w, "_busy", lambda: busy)
        monkeypatch.setattr(w, "_in_game", lambda: in_game)
        monkeypatch.setattr(w, "_modal_open", lambda: modal)
        with caplog.at_level(logging.INFO, logger="lolhex.ui.mainwindow"):
            w._apply_cloud({"features": {}})
        msgs = [r.getMessage() for r in caplog.records if "云控公告/版本检查推迟" in r.getMessage()]
        assert msgs == [f"{why}，云控公告/版本检查推迟"]
        assert w._deferred_cloud is not None
    finally:
        ov.timer.stop()
        ov.set_visible(False)
        w.close()
        for widget in (w, ov.panel, ov.badges, ov.champ):
            widget.deleteLater()
        app.processEvents()
