"""1.1.1（A2）：所有设置都能云控。defaults 只改玩家没动过的项；force 盖掉但不写进设置文件，撤掉就恢复。"""
import json

from lolhex import cloud, cloudsettings
from lolhex.config import Settings

V = "1.1.1"


def cfg(*rules):
    return cloud._merge({"settings": list(rules)})


def test_defaults_skip_items_the_player_changed(tmp_path):
    s = Settings.load(tmp_path / "config.json")
    s.mark_user("show_winrate")
    res = cloudsettings.apply(s, cfg({"defaults": {"speed": "saver", "show_winrate": False}}), V)
    assert res["changed"] == ["speed"] and s.speed == "saver" and s.show_winrate is True
    s.save(tmp_path / "config.json")
    assert Settings.load(tmp_path / "config.json").speed == "saver"  # defaults 写进设置文件


def test_force_overrides_but_is_not_saved_and_restores(tmp_path):
    p = tmp_path / "config.json"
    s = Settings.load(p)
    s.mark_user("overlay")
    res = cloudsettings.apply(s, cfg({"id": "r1", "force": {"overlay": False}, "reason": "浮窗临时关闭"}), V)
    assert s.overlay is False and res["reasons"] == [("r1", "浮窗临时关闭")]
    s.save(p)
    assert json.loads(p.read_text(encoding="utf-8"))["overlay"] is True  # 文件里还是玩家原来的
    # 断网重开：按上次存下的云控照样强制
    s2 = Settings.load(p)
    cloudsettings.apply(s2, cfg({"id": "r1", "force": {"overlay": False}}), V)
    assert s2.overlay is False
    # 云控撤掉：恢复
    res = cloudsettings.apply(s2, cfg(), V)
    assert s2.overlay is True and res["changed"] == ["overlay"] and not s2.forced


def test_versions_percent_and_bad_values(tmp_path):
    s = Settings.load(tmp_path / "config.json")
    cloudsettings.apply(s, cfg({"versions": ["1.1.2"], "defaults": {"speed": "low"}}), V)
    assert s.speed == "standard"
    cloudsettings.apply(s, cfg({"percent": 0, "defaults": {"speed": "low"}}), V)
    assert s.speed == "standard"
    bad = {"speed": "极快", "scan_interval_ms": 5, "match_threshold": "80", "overlay": "false",
           "install_id": "ffffffffffffffff", "send_usage": False, "riot_id": "x#1", "nope": 1,
           "smoothing_games": 500.7, "overlay_corner": "top-left"}
    res = cloudsettings.apply(s, cfg({"defaults": bad}), V)
    assert sorted(res["changed"]) == ["overlay_corner", "smoothing_games"]
    assert s.smoothing_games == 500 and s.overlay_corner == "top-left"
    assert s.install_id != "ffffffffffffffff" and s.send_usage is True and s.riot_id == ""


def test_percent_groups_by_install_id(tmp_path):
    hit = 0
    for i in range(200):
        s = Settings(install_id=f"{i:016x}")
        cloudsettings.apply(s, cfg({"id": "half", "percent": 50, "defaults": {"speed": "saver"}}), V)
        hit += s.speed == "saver"
    assert 60 < hit < 140
    s = Settings(install_id="0123456789abcdef", send_usage=False)  # 关了使用统计：算最后一组
    cloudsettings.apply(s, cfg({"percent": 99, "defaults": {"speed": "saver"}}), V)
    assert s.speed == "standard"


def test_old_config_migration_marks_non_default_as_user_set(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"speed": "low", "show_winrate": True, "install_id": "0123456789abcdef"}), encoding="utf-8")
    s = Settings.load(p)
    assert "speed" in s.user_set and "show_winrate" not in s.user_set
    cloudsettings.apply(s, cfg({"defaults": {"speed": "saver", "show_winrate": False}}), V)
    assert s.speed == "low" and s.show_winrate is False


def test_cloud_merge_keeps_rules_and_survives_garbage():
    assert cloud._merge({"settings": {"defaults": {"speed": "saver"}}})["settings"] == [{"defaults": {"speed": "saver"}}]
    assert cloud._merge({"settings": ["x", 3, {"force": {}}]})["settings"] == [{"force": {}}]
    assert cloud._merge({"settings": "坏的"})["settings"] == []
    s = Settings()
    assert cloudsettings.apply(s, cloud._merge({"settings": [{"defaults": "坏的", "force": [1]}]}), V)["changed"] == []
