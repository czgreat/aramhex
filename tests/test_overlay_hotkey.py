"""详情热键的真实窗口行为、前台边界、改键占用和原生消息；不向开发机发送按键。"""

import ctypes
import json
from ctypes import wintypes

import pytest
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QPushButton

from lolhex.config import DEFAULT_OVERLAY_HOTKEY, Settings
from lolhex.ui import hotkey
from test_mainwindow_cloud import mw
from test_overlay_on_demand import scene as base_scene


TEST_HOTKEY = "Ctrl+Alt+H"


@pytest.fixture
def scene(base_scene, monkeypatch):
    # 功能测试显式配置键位，不能依赖产品自动注册默认键。
    # 1.2.0 起快捷键默认不用，这些是云控 features.overlay_hotkey=true 退回 1.1.9 后的行为
    from lolhex import cloud
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge({"features": {"overlay_hotkey": True}}))
    base_scene.engine.settings.overlay_hotkey = TEST_HOTKEY
    return base_scene


def test_hotkey_off_by_default_even_if_set(base_scene, monkeypatch):
    """1.2.0：快捷键不好按，默认不用。玩家 1.1.9 设过的键也不注册，排队到的按键不展开详情；
    云控 features.overlay_hotkey=true 退回 1.1.9，设过的键照常生效。"""
    from lolhex import cloud
    cfg = {"features": {}}
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge(cfg))
    base_scene.engine.settings.overlay_hotkey = TEST_HOTKEY
    backend = Backend()
    base_scene.ov.hotkey.backend = backend
    for _ in range(3):
        base_scene.ov._tick()
    assert backend.bound is None and not backend.registrations
    base_scene.ov.hotkey.activated.emit()
    assert not base_scene.p.expanded
    cfg["features"]["overlay_hotkey"] = True
    base_scene.ov._tick()
    assert backend.bound


def test_settings_page_hides_hotkey_and_explains_mouse(mw, monkeypatch):
    """1.2.0：设置页不显示「详情快捷键」，「浮窗位置」下面写怎么用鼠标唤出；云控打开退回 1.1.9。"""
    from lolhex import cloud
    window = mw[0]
    cfg = {"features": {}}
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge(cfg))
    window._show_hotkey_row()
    assert window.hotkey_box.isHidden() and window.hotkey_label.isHidden()
    assert not window.corner_hint.isHidden() and "鼠标移到" in window.corner_hint.text()
    cfg["features"]["overlay_hotkey"] = True
    window._apply_cloud(cfg)
    assert not window.hotkey_box.isHidden() and not window.hotkey_label.isHidden()
    assert window.corner_hint.isHidden()


class Backend:
    def __init__(self):
        self.bound = None
        self.registrations = []
        self.conflict = False

    def register(self, shortcut):
        error = self.probe(shortcut)
        if not error:
            self.bound = shortcut
            self.registrations.append(shortcut.text)
        return error

    def probe(self, shortcut):
        return "这个快捷键已被其他程序占用，请换一个组合键。" if self.conflict else ""

    def release(self):
        self.bound = None

    close = release


@pytest.mark.parametrize("text,mods,key", [
    ("Ctrl+Shift+F8", 6, 0x77), ("Alt+F11", 1, 0x7A),
    ("Ctrl+Alt+H", 3, 0x48), ("Ctrl+1", 2, 0x31),
])
def test_shortcut_maps_to_windows_keys(text, mods, key):
    shortcut = hotkey.parse_shortcut(text)
    assert (shortcut.modifiers, shortcut.key) == (mods, key)
    assert hotkey.parse_shortcut(shortcut.text) == shortcut


@pytest.mark.parametrize("text", ["F8", "Shift+Q", "Ctrl+F12", "Meta+H", "Ctrl+Tab",
                                      "Ctrl+X, Ctrl+H", "bad-key", "Ctrl+", None, 123])
def test_unsafe_or_unsupported_shortcuts_rejected(text):
    with pytest.raises(ValueError):
        hotkey.parse_shortcut(text)


def test_upgrade_and_disable_survive_save(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"badge_position": "bottom", "calibration_extra": [1, 2]}), encoding="utf-8")
    settings = Settings.load(path)
    assert settings.overlay_hotkey == DEFAULT_OVERLAY_HOTKEY and settings.badge_position == "bottom"
    settings.set_by_user("overlay_hotkey", "")
    settings.save(path)
    loaded = Settings.load(path)
    assert loaded.overlay_hotkey == "" and loaded.extra["calibration_extra"] == [1, 2]
    assert hotkey.parse_shortcut(loaded.overlay_hotkey) is None


@pytest.mark.parametrize("value,user_set,expected", [
    ("Ctrl+Shift+F8", None, ""),
    ("Ctrl+Shift+F8", [], ""),
    ("Ctrl+Shift+F8", "invalid", ""),
    ("Ctrl+Shift+F8", ["overlay_hotkey"], "Ctrl+Shift+F8"),
    (TEST_HOTKEY, None, TEST_HOTKEY),
    (TEST_HOTKEY, [], TEST_HOTKEY),
    ("", None, ""),
    ("", ["overlay_hotkey"], ""),
])
def test_only_unmarked_legacy_f8_default_is_migrated(tmp_path, value, user_set, expected):
    path = tmp_path / "config.json"
    data = {"overlay_hotkey": value, "install_id": "a" * 16,
            "overlay_on_demand": True, "calibration_extra": [1, 2]}
    if user_set is not None:
        data["user_set"] = user_set
    path.write_text(json.dumps(data), encoding="utf-8")
    settings = Settings.load(path)
    assert settings.overlay_hotkey == expected
    assert settings.overlay_on_demand and settings.extra["calibration_extra"] == [1, 2]
    if value != expected:
        assert json.loads(path.read_text(encoding="utf-8"))["overlay_hotkey"] == ""
        assert "overlay_hotkey" not in settings.user_set
    settings.save(path)
    assert Settings.load(path).overlay_hotkey == expected


def test_unbound_default_keeps_mouse_entry_and_never_registers(base_scene, monkeypatch):
    from PySide6.QtCore import QPoint

    assert DEFAULT_OVERLAY_HOTKEY == "" and Settings().overlay_hotkey == ""
    backend = Backend()
    base_scene.ov.hotkey.backend = backend
    base_scene.ov._tick()
    assert not backend.registrations and backend.bound is None
    assert "已禁用" in base_scene.ov.hotkey.status
    monkeypatch.setattr(base_scene.p, "give_back_focus", lambda: None)
    base_scene.cursor[0] = QPoint(base_scene.rect.right - 32, base_scene.rect.top + 110)
    base_scene.ov._tick()
    base_scene.p.btn.click()
    assert base_scene.p.expanded and base_scene.p.body_win.isVisible()
    assert not backend.registrations


def test_hotkey_opens_hidden_details_and_does_not_change_game_focus(scene, monkeypatch):
    backend = Backend()
    scene.ov.hotkey.backend = backend
    focus_calls = []
    monkeypatch.setattr(scene.p, "give_back_focus", lambda: focus_calls.append(True))
    scene.ov._tick()
    assert backend.bound and not scene.p.isVisible()
    scene.ov.hotkey.activated.emit()
    assert scene.p.isVisible() and scene.p.expanded and scene.p.body_win.isVisible()
    assert "出装" in scene.p.info.text() or "加点" in scene.p.info.text()
    scene.ov.hotkey.activated.emit()
    assert not scene.p.isVisible() and not scene.p.body_win.isVisible()
    assert focus_calls == []


def test_background_and_master_off_release_and_cannot_reopen(scene):
    backend = Backend()
    scene.ov.hotkey.backend = backend
    scene.ov._tick()
    assert backend.bound
    scene.front[0] = False
    # 已排队的激活即使在下一个 250ms tick 前到达，也不能展开。
    scene.ov.hotkey.activated.emit()
    assert not scene.p.expanded and backend.bound is None
    scene.ov._tick()
    scene.front[0] = True
    scene.ov._tick()
    assert backend.bound
    scene.ov.set_visible(False)
    scene.ov.hotkey.activated.emit()
    assert not scene.p.expanded and not scene.p.isVisible() and backend.bound is None


def test_live_change_disable_and_no_registration_loop(scene):
    backend = Backend()
    scene.ov.hotkey.backend = backend
    for _ in range(5):
        scene.ov._tick()
    assert backend.registrations == [TEST_HOTKEY]
    scene.engine.settings.overlay_hotkey = "Alt+F10"
    scene.ov._tick()
    assert backend.bound.text == "Alt+F10"
    scene.engine.settings.overlay_hotkey = ""
    scene.ov._tick()
    assert backend.bound is None and "已禁用" in scene.ov.hotkey.status
    scene.engine.settings.overlay_hotkey = "Ctrl+F12"
    scene.ov._tick()
    assert backend.bound is None and "保留键" in scene.ov.hotkey.status


def test_disabling_hotkey_keeps_automatic_augment_recommendations(scene):
    scene.engine.settings.overlay_hotkey = ""
    scene.p._first_offer_done = True  # 不是这局第一次选择（第一次结束会展开出装，见 test_overlay_first_offer）
    scene.engine._set(offer={"boxes": [], "slots": ["1029", None, None]},
                      evaluation={"slots": []})
    scene.ov._tick()
    assert scene.ov.badges.isVisible() and scene.p.isVisible()
    scene.engine._set(offer=None, evaluation=None)
    scene.ov._tick()
    assert not scene.ov.badges.isVisible() and not scene.p.isVisible()


def test_conflict_is_visible_and_retried_on_foreground_return(scene):
    backend = Backend()
    backend.conflict = True
    scene.ov.hotkey.backend = backend
    for _ in range(5):
        scene.ov._tick()
    assert backend.bound is None and "占用" in scene.ov.hotkey.status
    backend.conflict = False
    scene.front[0] = False
    scene.ov._tick()
    assert "占用" in scene.ov.hotkey.status  # 切到设置页仍能看到上次失败原因。
    scene.front[0] = True
    scene.ov._tick()
    assert backend.bound


def test_champion_select_hotkey_only_while_actually_selecting(scene, monkeypatch):
    backend = Backend()
    scene.ov.hotkey.backend = backend
    monkeypatch.setattr(scene.ov.champ, "update_from", lambda *a: None)
    monkeypatch.setattr(scene.ov.autoshot, "tick", lambda *a: None)
    monkeypatch.setattr("lolhex.ui.overlay.win.find_game_window", lambda: None)
    scene.engine._set(phase="client", champ_select={"mine": "17"})
    scene.ov._tick()
    assert backend.bound and scene.p.expanded
    scene.ov.hotkey.activated.emit()
    assert not scene.p.expanded
    scene.engine._set(champ_select=None)
    scene.ov._tick()
    assert backend.bound is None


def test_hotkey_applies_right_away_and_bad_keys_keep_the_old_one(mw, monkeypatch):
    """没有「保存」按钮：录完组合键马上生效并写进设置文件；被占用、单按的键不改原来的设置，旁边一行说明原因。"""
    window, settings, engine, _, home = mw
    monkeypatch.setattr("lolhex.ui.mainwindow.autostart.set_enabled", lambda value: None)
    warnings = []
    monkeypatch.setattr("lolhex.ui.mainwindow.QMessageBox.warning", lambda *a: warnings.append(a[-1]))
    backend = Backend()
    window.overlay.hotkey.backend = backend
    window.set_hotkey.setKeySequence(QKeySequence("Ctrl+Alt+H"))
    assert settings.overlay_hotkey == "Ctrl+Alt+H" and "overlay_hotkey" in settings.user_set
    assert Settings.load(home / "config.json").overlay_hotkey == "Ctrl+Alt+H"
    backend.conflict = True
    window.set_hotkey.setKeySequence(QKeySequence("Alt+F10"))
    assert "占用" in window.hotkey_status.text() and not warnings  # 不弹窗，下面一行写原因
    assert settings.overlay_hotkey == "Ctrl+Alt+H"
    assert Settings.load(home / "config.json").overlay_hotkey == "Ctrl+Alt+H"
    window.set_hotkey.setKeySequence(QKeySequence("F8"))
    assert "Ctrl 或 Alt" in window.hotkey_status.text() and settings.overlay_hotkey == "Ctrl+Alt+H"
    clear = next(b for b in window.findChildren(QPushButton) if b.text() == "清空")
    clear.click()
    assert settings.overlay_hotkey == DEFAULT_OVERLAY_HOTKEY == ""
    assert Settings.load(home / "config.json").overlay_hotkey == ""
    assert not [b for b in window.findChildren(QPushButton) if b.text() in ("恢复默认", "保存设置")]


class WinFunction:
    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


def test_native_registration_no_repeat_ownership_and_queued_messages(monkeypatch):
    app = QApplication.instance() or QApplication([])
    register, unregister = WinFunction(True), WinFunction(True)
    api = type("API", (), {"RegisterHotKey": register, "UnregisterHotKey": unregister})()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: api, raising=False)
    activated = []
    binding = hotkey._NativeHotkey(app, lambda: activated.append(True))
    try:
        shortcut = hotkey.parse_shortcut(TEST_HOTKEY)
        assert binding.register(shortcut) == ""
        assert register.calls[-1] == (None, binding.ident, shortcut.modifiers | hotkey.MOD_NOREPEAT, shortcut.key)
        original_ident = binding.ident
        assert binding.probe(shortcut) == ""  # 本实例占有的键不会误报冲突。
        assert len(register.calls) == 1
        msg = wintypes.MSG()
        msg.message, msg.wParam = hotkey.WM_HOTKEY, binding.ident
        msg.lParam = shortcut.modifiers | shortcut.key << 16
        assert binding.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(msg)) == (True, 0)
        app.processEvents()
        assert activated == [True]
        # 排队后马上改键 / 释放：旧事件不能调用业务逻辑。
        binding.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(msg))
        binding.release()
        binding.register(shortcut)  # 同一个键重新注册，也不能执行上一代排队的回调。
        assert binding.ident != original_ident
        app.processEvents()
        assert activated == [True]
        # 旧消息还在 Win32 队列、重注册后才进入 filter：必须按旧 id 丢弃。
        assert binding.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(msg)) == (False, 0)
        app.processEvents()
        assert activated == [True]
        msg.wParam = binding.ident
        binding.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(msg))
        app.processEvents()
        assert activated == [True, True]
        binding.release()
        assert unregister.calls[-1] == (None, binding.ident)
        register.result = False
        monkeypatch.setattr(ctypes, "get_last_error", lambda: 1409, raising=False)
        assert "占用" in binding.register(shortcut) and binding.bound is None
        assert "占用" in binding.probe(shortcut)
        assert len(unregister.calls) == 2  # 注册失败不注销其他注册者。
    finally:
        binding.close()


def test_native_id_exhaustion_stops_instead_of_reusing_ids(monkeypatch):
    app = QApplication.instance() or QApplication([])
    register, unregister = WinFunction(True), WinFunction(True)
    api = type("API", (), {"RegisterHotKey": register, "UnregisterHotKey": unregister})()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: api, raising=False)
    monkeypatch.setattr(hotkey, "_ids", iter([0xBFFE, 0xBFFF]))
    binding = hotkey._NativeHotkey(app, lambda: None)
    try:
        shortcut = hotkey.parse_shortcut(TEST_HOTKEY)
        assert binding.register(shortcut) == "" and binding.ident == 0xBFFF
        assert "重启助手" in binding.register(shortcut) and binding.bound is None
        assert len(register.calls) == 1
    finally:
        binding.close()
