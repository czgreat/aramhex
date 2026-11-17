"""按需按钮真正隐藏窗口，同时保留选人、三选一和手动选英雄提示。"""
import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from lolhex import cloudsettings
from lolhex.config import Settings
from lolhex.engine import Engine
from lolhex.ui import overlay
from lolhex.vision import window


@pytest.fixture
def scene(store, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    clock, cursor, front = [100.0], [QPoint(-9999, -9999)], [True]
    rect = window.Rect(-640, 100, 1920, 1080)
    monkeypatch.setattr(window, "IS_WINDOWS", False)
    monkeypatch.setattr(window, "watch_foreground", lambda: None)
    monkeypatch.setattr(window, "_drawn", {})
    monkeypatch.setattr(window, "find_game_window", lambda: 1)
    monkeypatch.setattr(window, "find_client_window", lambda: 2)
    monkeypatch.setattr(window, "client_rect", lambda hwnd: rect)
    monkeypatch.setattr(window, "is_foreground", lambda hwnd: front[0])
    monkeypatch.setattr(overlay.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(overlay.QCursor, "pos", staticmethod(lambda: cursor[0]))
    engine = Engine(Settings(overlay_on_demand=True), home=tmp_path, store=store)
    engine._set(phase="ingame", hero_id="17")
    ov = overlay.Overlay(engine)
    ov.timer.stop()
    # 这些测试看的是第一次海克斯选完以后的按需行为；对局开始时的固定显示在 test_overlay_first_offer.py
    ov.panel._ingame_since, ov.panel._pin_over = clock[0], True
    # Offscreen geometry tests use one logical pixel per physical pixel.
    monkeypatch.setattr(overlay, "_dpr", lambda: 1.0)
    monkeypatch.setattr(ov.panel, "devicePixelRatioF", lambda: 1.0)
    yield SimpleNamespace(app=app, clock=clock, cursor=cursor, front=front,
                          rect=rect, engine=engine, ov=ov, p=ov.panel)
    ov.set_visible(False)
    for widget in (ov.panel, ov.badges, ov.champ):
        widget.close()
        widget.deleteLater()
    app.processEvents()


def test_idle_ticks_never_show_a_window(scene, monkeypatch):
    shows = []
    original = scene.p.show
    monkeypatch.setattr(scene.p, "show", lambda: (shows.append(True), original()))
    for _ in range(120):
        scene.ov._tick()
        scene.clock[0] += .25
    assert shows == []
    assert not any(w.isVisible() for w in
                   (scene.p, scene.p.body_win, scene.ov.badges, scene.ov.champ))
    assert window.drawn_rects() == []


def test_default_mode_and_live_toggle(scene):
    scene.engine.settings.overlay_on_demand = False
    scene.ov._tick()
    assert scene.p.isVisible()
    scene.engine.settings.overlay_on_demand = True
    scene.ov._tick()
    assert not scene.p.isVisible()
    scene.engine.settings.overlay_on_demand = False
    scene.ov._tick()
    assert scene.p.isVisible()


def test_hover_reveals_then_click_keeps_details_available(scene, monkeypatch):
    monkeypatch.setattr(scene.p, "give_back_focus", lambda: None)
    scene.cursor[0] = QPoint(scene.rect.right - 32, scene.rect.top + 110)
    scene.ov._tick()
    assert scene.p.isVisible() and not scene.p.expanded
    scene.cursor[0] = QPoint(-9999, -9999)
    scene.clock[0] += 1.0
    scene.ov._tick()
    assert scene.p.isVisible()  # 留出点击时间，移出热区不马上消失。
    scene.p.btn.click()
    scene.clock[0] += 2.0
    scene.ov._tick()
    assert scene.p.expanded and scene.p.isVisible() and scene.p.body_win.isVisible()
    scene.clock[0] += overlay.AUTO_HIDE_SECONDS + .1
    scene.ov._tick()
    assert not scene.p.expanded and not scene.p.isVisible()
    assert not scene.p.body_win.isVisible()


@pytest.mark.parametrize("corner", ["top-left", "top-right"])
@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
def test_physical_hover_coordinates_and_expiry(scene, monkeypatch, corner, scale):
    monkeypatch.setattr(window, "IS_WINDOWS", True)
    monkeypatch.setattr(scene.p, "devicePixelRatioF", lambda: scale)
    rect = window.Rect(-2560, -1440, 2560, 1440)
    scene.p.corner = corner
    x = rect.left + round(32 * scale) if corner == "top-left" else rect.right - round(32 * scale)
    cursor = [(x, rect.top + round(110 * scale))]
    monkeypatch.setattr(window, "cursor_pos", lambda: cursor[0])
    snap = scene.engine.snapshot()
    assert scene.p.button_needed(snap, rect, True)
    cursor[0] = (-9999, -9999)
    scene.clock[0] += overlay.BUTTON_REVEAL_SECONDS - .01
    assert scene.p.button_needed(snap, rect, True)
    scene.clock[0] += .02
    assert not scene.p.button_needed(snap, rect, True)


def test_hover_lease_resets_on_window_move_and_background(scene):
    snap = scene.engine.snapshot()
    scene.cursor[0] = QPoint(scene.rect.right - 32, scene.rect.top + 110)
    assert scene.p.button_needed(snap, scene.rect, True)
    moved = window.Rect(2000, 100, 1920, 1080)
    assert not scene.p.button_needed(snap, moved, True)
    assert scene.p.button_needed(snap, scene.rect, True)
    assert not scene.p.button_needed(snap, scene.rect, False)
    scene.cursor[0] = QPoint(-9999, -9999)
    assert not scene.p.button_needed(snap, scene.rect, True)


def test_hidden_native_window_follows_target_without_showing(scene, monkeypatch):
    monkeypatch.setattr(window, "IS_WINDOWS", True)
    native = [window.Rect(0, 0, 40, 40)]
    moves = []
    monkeypatch.setattr(window, "window_rect", lambda hwnd: native[0])

    def move(hwnd, rect):
        moves.append(rect)
        native[0] = rect

    monkeypatch.setattr(window, "set_window_rect", move)
    scene.p.place(scene.rect)
    assert not scene.p.isVisible() and not scene.p.body_win.isVisible()
    assert native[0] == window.Rect(scene.rect.right - 52, scene.rect.top + 90, 40, 40)
    assert len(moves) == 1
    scene.p.place(scene.rect)
    assert len(moves) == 1  # 位置不变时不反复 SetWindowPos。


def test_hover_cannot_bypass_master_switch(scene):
    scene.cursor[0] = QPoint(scene.rect.right - 32, scene.rect.top + 110)
    scene.ov.set_visible(False)
    scene.ov._tick()
    assert not scene.p.isVisible()
    scene.ov.set_visible(True)
    assert scene.p.isVisible()


def test_offer_and_foreground_visibility_do_not_need_new_recognition(scene):
    offer = {"boxes": [[700, 430, 1000, 470]], "slots": ["1029"]}
    ev = {"advice": "参考", "slots": [{"index": 0, "verdict": "best", "score": 2}]}
    scene.p._first_offer_done = True  # 不是这局第一次选择（第一次结束会展开出装，见 test_overlay_first_offer）
    scene.engine._set(offer=offer, evaluation=ev)
    scene.ov._tick()
    assert scene.p.isVisible() and scene.ov.badges.isVisible()
    scene.front[0] = False
    scene.ov._tick()
    assert not scene.p.isVisible() and not scene.ov.badges.isVisible()
    assert scene.engine.state["offer"] is offer
    scene.front[0] = True
    scene.ov._tick()
    assert scene.p.isVisible() and scene.ov.badges.isVisible()
    scene.engine._set(offer=None, evaluation=None)
    scene.ov._tick()
    assert not scene.p.isVisible() and not scene.ov.badges.isVisible()


def test_unknown_hero_prompt_remains_until_choice_or_manual_close(scene):
    scene.engine._set(hero_id=None)
    scene.ov._tick()
    assert scene.p.isVisible() and scene.p.expanded and scene.p.body_win.isVisible()
    scene.clock[0] += 60
    scene.ov._tick()
    assert scene.p.expanded
    scene.p.toggle()
    scene.ov._tick()
    assert not scene.p.isVisible() and not scene.p.expanded
    scene.ov._tick()
    assert not scene.p.expanded  # 手动收起后不重复弹。


def test_champion_select_keeps_winrates_and_details(scene, monkeypatch):
    monkeypatch.setattr(window, "find_game_window", lambda: 0)
    cs = {"bench": [], "team": [], "cards": [], "mine": "17"}
    scene.engine._set(phase="client", champ_select=cs)
    scene.ov._tick()
    assert scene.p.isVisible() and scene.p.expanded
    assert "胜率" in scene.p.hero_label.text()
    assert scene.ov.champ.isVisible() and scene.ov.champ.show_win


def test_master_switch_and_settings_save_do_not_flash_hidden_button(scene, monkeypatch):
    shows = []
    original = scene.p.show
    monkeypatch.setattr(scene.p, "show", lambda: (shows.append(True), original()))
    scene.ov.set_visible(False)
    scene.ov.set_visible(True)
    scene.ov.set_visible(True)
    assert not scene.p.isVisible() and shows == []


@pytest.mark.parametrize("value", [None, False, "true", 1, [], {}])
def test_on_demand_is_always_on_after_load(tmp_path, value):
    """1.1.7 起按需按钮固定开启：旧设置文件、测试版里取消过勾选的，读出来都是开着，也不算玩家改的。"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"badge_position": "bottom", "overlay_on_demand": value,
                                "user_set": ["badge_position", "overlay_on_demand"]}), encoding="utf-8")
    settings = Settings.load(path)
    assert settings.overlay_on_demand is True and settings.badge_position == "bottom"
    assert settings.user_set == ["badge_position"]
    path.write_text('{"overlay":true,"extra_future_key":23}', encoding="utf-8")
    settings = Settings.load(path)
    settings.save(path)
    assert settings.overlay_on_demand is True
    assert json.loads(path.read_text(encoding="utf-8"))["extra_future_key"] == 23


def test_on_demand_setting_roundtrip_and_cloud_restore(tmp_path):
    path = tmp_path / "config.json"
    settings = Settings(overlay_on_demand=True)
    cloudsettings.apply(settings, {"settings": [{"force": {"overlay_on_demand": False}}]}, "1.1.6")
    settings.save(path)
    assert settings.overlay_on_demand is False
    assert Settings.load(path).overlay_on_demand is True
    cloudsettings.apply(settings, {}, "1.1.6")
    assert settings.overlay_on_demand is True
