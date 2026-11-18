"""主窗口里的云控：设置（A2）和查分库更新后自动重查（A9）。无界面模式跑真主窗口。"""
import json
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
