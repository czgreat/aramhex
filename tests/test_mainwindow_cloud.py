"""主窗口里的云控：设置（A2）和查分库更新后自动重查（A9）。无界面模式跑真主窗口。"""
import json
import re
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def mw(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from lolhex import cloud
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui import mainwindow, overlay
    monkeypatch.setattr(mainwindow.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    s = Settings.load()
    eng = Engine(s)
    ov = overlay.Overlay(eng)
    w = mainwindow.MainWindow(eng, ov)
    toasts = []
    w._toast = toasts.append
    w.toasts = toasts
    yield w, s, eng, cloud, tmp_path
    ov.timer.stop()
    ov.set_visible(False)
    w.close()
    for widget in (w, ov.panel, ov.badges, ov.champ):
        widget.deleteLater()
    app.processEvents()


def test_cloud_force_then_player_change_then_removed(mw, monkeypatch):
    w, s, _, cloud, home = mw
    cfg = cloud._merge({"settings": [{"id": "t1", "defaults": {"speed": "saver"},
                                      "force": {"badge_position": "bottom"}, "reason": "测试原因"}]})
    monkeypatch.setattr(cloud, "get", lambda: cfg)
    w._apply_cloud_settings(cfg)
    w._apply_cloud_settings(cfg)
    assert w.toasts == ["测试原因"]  # 同一条规则只提示一次
    assert s.speed == "saver" and not hasattr(w, "set_speed") and w.set_badge.currentData() == "bottom"
    assert "speed" not in s.user_set  # 云控改的不算玩家改的
    w.set_badge.setCurrentIndex(w.set_badge.findData("top"))  # 没有保存按钮：一改就生效
    assert s.badge_position == "bottom" and s.forced == {"badge_position": "top"}
    assert json.loads((home / "config.json").read_text(encoding="utf-8"))["badge_position"] == "top"
    empty = cloud._merge({})
    monkeypatch.setattr(cloud, "get", lambda: empty)
    w._apply_cloud_settings(empty)
    assert s.badge_position == "top" and w.set_badge.currentData() == "top" and s.speed == "saver"


def test_turning_usage_off_drops_ids_rule_at_once(mw, monkeypatch):
    """1.1.9：按编号挑的规则，玩家关掉使用统计（没有编号了）马上撤，不等下次刷新云控；再打开马上回来。"""
    from lolhex import __version__
    w, s, _, cloud, _ = mw
    s.send_usage = True
    w.set_usage.blockSignals(True)
    w.set_usage.setChecked(True)
    w.set_usage.blockSignals(False)
    cfg = cloud._merge({"settings_v2": [{"id": "pick", "versions": [__version__], "ids": [s.install_id], "force": {"vision_priority": "normal"}}]})
    monkeypatch.setattr(cloud, "get", lambda: cfg)
    w._apply_cloud_settings(cfg)
    assert s.vision_priority == "normal" and "vision_priority" in s.forced
    w.set_usage.setChecked(False)
    assert s.send_usage is False and s.vision_priority == "below_normal" and not s.forced
    w.set_usage.setChecked(True)
    assert s.vision_priority == "normal"


def test_cloud_force_reason_waits_until_the_game_is_over(mw, monkeypatch):
    """1.1.8：云控强制的原因在对局中不提示，回到游戏外再出，也只出一次。"""
    w, s, eng, cloud, _ = mw
    cfg = cloud._merge({"settings": [{"id": "t-ingame", "force": {"badge_position": "bottom"}, "reason": "打完再说"}]})
    monkeypatch.setattr(cloud, "get", lambda: cfg)
    phase = {"phase": "ingame"}
    monkeypatch.setattr(eng, "snapshot", lambda: dict(phase))
    w._apply_cloud_settings(cfg)
    assert w.toasts == [] and s.badge_position == "bottom"  # 设置照样马上生效
    w._notify_tick()
    assert w.toasts == []
    phase["phase"] = "lobby"
    w._notify_tick()
    w._apply_cloud_settings(cfg)
    assert w.toasts == ["打完再说"]


def test_idle_guard_has_no_checkbox_and_only_cloud_can_turn_it_on(mw):
    """空闲时减少识别开销：设置页没有开关；测试包里勾过的读设置时关掉；云控还能打开、撤掉后关回去。"""
    w, s, _, cloud, home = mw
    import json
    from lolhex.config import Settings
    assert not hasattr(w, "set_idle_guard") and s.idle_ocr_guard is False
    (home / "old.json").write_text(json.dumps({"idle_ocr_guard": True, "user_set": ["idle_ocr_guard"]}), encoding="utf-8")
    old = Settings.load(home / "old.json")
    assert old.idle_ocr_guard is False and "idle_ocr_guard" not in old.user_set
    w._apply_cloud_settings(cloud._merge({"settings": [{"force": {"idle_ocr_guard": True}}]}))
    assert s.idle_ocr_guard is True
    w._apply_cloud_settings(cloud._merge({}))
    assert s.idle_ocr_guard is False


def test_on_demand_has_no_checkbox_and_cloud_force_still_works(mw):
    """1.1.7 起按需按钮固定开启，设置页没有勾选框；云控 force 仍可应急关掉、撤掉后恢复。"""
    w, s, _, cloud, _ = mw
    assert not hasattr(w, "set_on_demand") and s.overlay_on_demand is True
    w._apply_cloud_settings(cloud._merge({"settings": [{"force": {"overlay_on_demand": False}}]}))
    assert s.overlay_on_demand is False
    w._apply_cloud_settings(cloud._merge({}))
    assert s.overlay_on_demand is True


def test_every_setting_saves_on_change(mw, monkeypatch):
    """改哪一项都马上写进设置文件；开机自启单独处理，失败时勾选框退回原状。"""
    w, s, _, cloud, home = mw
    from lolhex.config import Settings
    from lolhex.ui import mainwindow
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge({}))
    w.set_badge.setCurrentIndex(w.set_badge.findData("bottom"))
    w.set_corner.setCurrentIndex(1)
    w.set_data.setChecked(False)
    saved = Settings.load(home / "config.json")
    assert (saved.badge_position, saved.overlay_corner, saved.check_data_updates) == ("bottom", "top-left", False)
    assert w.overlay.panel.corner == "top-left"
    calls = []
    monkeypatch.setattr(mainwindow.autostart, "set_enabled", calls.append)
    monkeypatch.setattr(mainwindow.autostart, "is_enabled", lambda: bool(calls and calls[-1]))  # 失败时按注册表实际状态改回
    w.set_auto.setChecked(True)
    assert calls == [True]
    warnings = []
    monkeypatch.setattr(mainwindow.QMessageBox, "warning", lambda *a: warnings.append(a[-1]))
    monkeypatch.setattr(mainwindow.autostart, "set_enabled", lambda on: (_ for _ in ()).throw(OSError("拒绝访问")))
    w.set_auto.setChecked(False)
    assert w.set_auto.isChecked() is True and "拒绝访问" in warnings[-1]
    assert not [b for b in w.findChildren(mainwindow.QPushButton) if b.text() == "保存设置"]


def test_rank_rechecked_when_rank_db_newer(mw):
    w, s, eng, cloud, _ = mw
    s.riot_id = "提莫#1"
    s.my_rank = {"as_of": "2026-10-16", "results": [{"band": "mid", "band_name": "中分", "top": 0.2,
                                                    "region_name": "艾欧尼亚", "games": 50}]}
    eng.set_band("mid")
    calls = []

    def fake(silent=False):
        calls.append(silent)
        w._rank_done("提莫#1", {"as_of": "2026-10-22", "results": [{"band": "high", "band_name": "前 5%", "top": 0.031,
                                                                   "region_name": "艾欧尼亚", "games": 80}]}, silent)
    w._query_rank = fake
    cfg = cloud._merge({"rank": {"as_of": "2026-10-22"}})
    assert w.recheck_rank_if_newer(cfg) and s.data_band == "high"  # 分段原来跟着查分选的：跟着换
    assert w.toasts and "前 5%" in w.toasts[0] and "中分" in w.toasts[0]
    assert not w.recheck_rank_if_newer(cfg) and calls == [True]   # 同一个日期只重查一次
    assert not w.recheck_rank_if_newer(cloud._merge({"rank": {"as_of": "2026-10-20"}}))
    # 玩家自己选了别的分段：只提示，不换
    s.my_rank = {"as_of": "2026-10-01", "results": [{"band": "mid", "band_name": "中分", "top": 0.2,
                                                    "region_name": "艾欧尼亚", "games": 50}]}
    s.extra.pop("rank_rechecked")
    eng.set_band("casual")
    w.recheck_rank_if_newer(cfg)
    assert s.data_band == "casual" and len(w.toasts) == 2
    # 没填过查分账号：不查
    s.riot_id = ""
    s.extra.pop("rank_rechecked")
    assert not w.recheck_rank_if_newer(cfg)


@pytest.fixture
def registry(monkeypatch):
    """假的开机自启注册表：fail=True 时写不进去。"""
    from lolhex.ui import autostart
    reg = {"on": False, "fail": False}

    def set_enabled(on):
        if reg["fail"]:
            raise PermissionError("拒绝访问")
        reg["on"] = on
    monkeypatch.setattr(autostart, "is_enabled", lambda: reg["on"])
    monkeypatch.setattr(autostart, "set_enabled", set_enabled)
    return reg


def _tray(w, eng):
    from lolhex.ui import mainwindow
    tray = mainwindow.Tray(eng, w, w.overlay)
    tray.timer.stop()
    w.tray = tray
    return tray


def test_tray_has_no_overlay_switch(mw, registry):
    """1.2.0：托盘去掉「显示游戏浮窗」（开着助手就显示浮窗）。"""
    w, _, eng, _, _ = mw
    tray = _tray(w, eng)
    names = [a.text() for a in tray.contextMenu().actions() if not a.isSeparator()]
    assert names == ["打开主界面", "重新识别", "检查更新", "开机自动启动", "退出"]


def test_autostart_in_tray_and_settings_stay_in_sync(mw, registry, monkeypatch):
    """1.2.0：设置页和托盘的「开机自动启动」任何一处改了，另一处马上跟着变；写不进去两处都改回去并提示。"""
    from lolhex.ui import mainwindow
    w, _, eng, _, _ = mw
    tray = _tray(w, eng)
    w.set_auto.setChecked(True)
    assert registry["on"] and tray.act_auto.isChecked()
    tray.act_auto.setChecked(False)
    assert not registry["on"] and not w.set_auto.isChecked()

    messages, warnings = [], []
    monkeypatch.setattr(tray, "showMessage", lambda *a: messages.append(a[1]))
    monkeypatch.setattr(mainwindow.QMessageBox, "warning", staticmethod(lambda *a: warnings.append(a[2])))
    registry["fail"] = True
    tray.act_auto.setChecked(True)
    assert not tray.act_auto.isChecked() and not w.set_auto.isChecked() and "拒绝访问" in messages[0]
    w.set_auto.setChecked(True)
    assert not w.set_auto.isChecked() and not tray.act_auto.isChecked() and "拒绝访问" in warnings[0]

    registry.update(fail=False, on=True)  # 别的程序改了注册表：打开托盘菜单时按实际状态显示
    tray.contextMenu().aboutToShow.emit()
    assert tray.act_auto.isChecked()


def test_settings_page_shows_saved(mw):
    """1.2.0：设置页没有「保存」按钮，改完在检查更新下面短暂显示「已保存」，平时空着。"""
    w, _, _, _, _ = mw
    assert w.save_hint.text() == ""
    w.set_badge.setCurrentIndex(w.set_badge.findData("bottom"))
    assert "已保存" in w.save_hint.text() and w._saved_timer.isActive()
    w._saved_timer.timeout.emit()
    assert w.save_hint.text() == ""


def test_hotkey_placeholder_and_tiny_rank_percent(mw):
    """1.2.0：快捷键框的占位字是中文；前 0.05% 不再显示成 0.0%。"""
    w, _, _, _, _ = mw
    edit = w.set_hotkey.findChild(QtWidgets.QLineEdit)
    assert edit.placeholderText() == "点这里按下快捷键"
    w._show_rank({"results": [{"band_name": "顶尖", "top": 0.0005, "region_name": "艾欧尼亚", "games": 30}]})
    assert w.my_rank_label.text() == "你：顶尖（前 0.05%）"


def _panel_alpha(ov):
    """详情现在的底色不透明度（0～255），按 Qt 读颜色的方式算（8 位是 #AARRGGBB）。"""
    from PySide6.QtGui import QColor
    css = ov.panel.body.styleSheet()
    return QColor(re.search(r"QWidget#body\{background:(#[0-9a-f]+)", css).group(1)).alpha()


def test_panel_bg_button_cycles_and_saves(mw):
    """1.2.0：详情右下角「背景」按钮，点一下换下一档：透明（1.1.9，几乎透明）→ 半透明 → 不透明，
    马上生效并写进设置文件；设置页没有这一项。三选一标签的底色不变。"""
    from PySide6.QtWidgets import QLabel
    from lolhex.config import Settings
    from lolhex.ui import overlay
    w, s, _, cloud, home = mw
    ov = w.overlay
    btn = ov.panel.bg_btn
    assert not hasattr(w, "set_bg") and not any("底色" in l.text() or "背景" in l.text() for l in w.findChildren(QLabel))
    assert s.overlay_bg == "standard" and btn.text() == "背景：透明" and not btn.isHidden()
    assert ov.panel.PANEL_BG["standard"] == "#101318ee" and _panel_alpha(ov) == 0x10  # 1.1.9 原样
    btn.click()
    assert s.overlay_bg == "dark" and btn.text() == "背景：半透明" and 150 <= _panel_alpha(ov) < 255
    assert Settings.load(home / "config.json").overlay_bg == "dark" and "overlay_bg" in s.user_set
    btn.click()
    assert s.overlay_bg == "solid" and btn.text() == "背景：不透明" and _panel_alpha(ov) == 255
    assert not hasattr(ov.badges, "bg_level") and overlay.HINT_BG.alpha() == 170
    w.set_badge.setCurrentIndex(1 - w.set_badge.currentIndex())  # 改设置页别的项不碰详情的背景
    assert s.overlay_bg == "solid" and _panel_alpha(ov) == 255
    btn.click()
    assert s.overlay_bg == "standard" and btn.text() == "背景：透明" and _panel_alpha(ov) == 0x10
    assert Settings.load(home / "config.json").overlay_bg == "standard"


def test_cloud_sets_bg_default_but_cannot_force(mw, monkeypatch):
    """云控只能改详情背景的默认值（玩家没选过的才改），写在 force 里不生效。"""
    w, s, _, cloud, home = mw
    btn = w.overlay.panel.bg_btn
    cfg = cloud._merge({"settings": [{"id": "bg", "defaults": {"overlay_bg": "dark"}}]})
    monkeypatch.setattr(cloud, "get", lambda: cfg)
    w._apply_cloud_settings(cfg)
    assert s.overlay_bg == "dark" and btn.text() == "背景：半透明" and 150 <= _panel_alpha(w.overlay) < 255
    btn.click()
    btn.click()  # 玩家自己点回透明：以后云控改默认值不碰
    cfg = cloud._merge({"settings": [{"id": "bg", "defaults": {"overlay_bg": "solid"}, "force": {"overlay_bg": "solid"}}]})
    w._apply_cloud_settings(cfg)
    assert s.overlay_bg == "standard" and "overlay_bg" not in s.forced and btn.text() == "背景：透明"


def test_bg_feature_switch_hides_button(mw, monkeypatch):
    """云控 features.overlay_bg=false 退回 1.1.9：详情里不显示「背景」按钮，按透明画；
    玩家选的留着，开关打开后接着用。"""
    from lolhex.config import Settings
    w, s, _, cloud, home = mw
    btn = w.overlay.panel.bg_btn
    btn.click()
    btn.click()
    assert s.overlay_bg == "solid" and not btn.isHidden()
    off = cloud._merge({"features": {"overlay_bg": False}})
    monkeypatch.setattr(cloud, "get", lambda: off)
    w._apply_cloud(off)
    assert btn.isHidden() and _panel_alpha(w.overlay) == 0x10
    w.set_badge.setCurrentIndex(1 - w.set_badge.currentIndex())  # 改别的设置也不把背景变回不透明
    assert _panel_alpha(w.overlay) == 0x10
    assert s.overlay_bg == "solid" and Settings.load(home / "config.json").overlay_bg == "solid"
    on = cloud._merge({})
    monkeypatch.setattr(cloud, "get", lambda: on)
    w._apply_cloud(on)
    assert not btn.isHidden() and _panel_alpha(w.overlay) == 255


def test_overlay_bg_bad_values_fall_back(tmp_path):
    from lolhex import cloudsettings
    from lolhex.config import Settings
    p = tmp_path / "config.json"
    for bad in ("", "black", 80, None, True):
        p.write_text(json.dumps({"overlay_bg": bad}), encoding="utf-8")
        assert Settings.load(p).overlay_bg == "standard"
    check = cloudsettings.SPEC["overlay_bg"]
    assert check("dark") == "dark" and check("black") is None and check(1) is None
