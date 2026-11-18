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
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["speed"] == "saver"  # defaults 写进设置文件
    again = Settings.load(tmp_path / "config.json")
    assert again.speed == "standard"  # 1.1.7 起识别频率没有设置项：读设置时改回标准，云控每次启动再按规则改
    cloudsettings.apply(again, cfg({"defaults": {"speed": "saver"}}), V)
    assert again.speed == "saver"


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
    p.write_text(json.dumps({"badge_position": "bottom", "show_winrate": True, "install_id": "0123456789abcdef"}), encoding="utf-8")
    s = Settings.load(p)
    assert "badge_position" in s.user_set and "show_winrate" not in s.user_set
    cloudsettings.apply(s, cfg({"defaults": {"badge_position": "top", "show_winrate": False}}), V)
    assert s.badge_position == "bottom" and s.show_winrate is False


def test_cloud_merge_keeps_rules_and_survives_garbage():
    assert cloud._merge({"settings": {"defaults": {"speed": "saver"}}})["settings"] == [{"defaults": {"speed": "saver"}}]
    assert cloud._merge({"settings": ["x", 3, {"force": {}}]})["settings"] == [{"force": {}}]
    assert cloud._merge({"settings": "坏的"})["settings"] == []
    s = Settings()
    assert cloudsettings.apply(s, cloud._merge({"settings": [{"defaults": "坏的", "force": [1]}]}), V)["changed"] == []


# 1.1.9：按电脑挑选（系统、大小核、厂商、型号、编号），写在 settings_v2
W10_HYBRID = {"os": "10", "hybrid": True, "vendor": "intel", "cpu": "13th Gen Intel(R) Core(TM) i5-13400F"}
W11_AMD = {"os": "11", "hybrid": False, "vendor": "amd", "cpu": "AMD Ryzen 7 9800X3D 8-Core Processor"}
UNKNOWN = {"os": "", "hybrid": None, "vendor": "", "cpu": ""}
PRIO = {"force": {"vision_priority": "normal"}}
V9 = {"versions": ["1.1.9"]}  # settings_v2 每条都要写 versions


def v2(*rules):
    return cloud._merge({"settings_v2": list(rules)})


def _prio(rule, machine, install_id="0123456789abcdef", send_usage=True):
    s = Settings(install_id=install_id, send_usage=send_usage)
    cloudsettings.apply(s, v2({"id": "t", **V9, **rule, **PRIO}), "1.1.9", machine)
    return s.vision_priority


def test_machine_conditions_pick_only_matching_computers():
    rule = {"versions": ["1.1.9"], "os": ["10"], "hybrid": True}
    assert _prio(rule, W10_HYBRID) == "normal"
    assert _prio(rule, {**W10_HYBRID, "os": "11"}) == "below_normal"
    assert _prio(rule, {**W10_HYBRID, "hybrid": False}) == "below_normal"
    assert _prio(rule, UNKNOWN) == "below_normal"  # 拿不到系统、大小核：不算
    assert _prio({"hybrid": False}, W11_AMD) == "normal"
    assert _prio({"os": "11", "vendor": ["AMD"]}, W11_AMD) == "normal"  # 单个字符串、大小写都行
    assert _prio({"vendor": ["intel"]}, W11_AMD) == "below_normal"
    assert _prio({"cpu": ["x3d", "13400F"]}, W11_AMD) == "normal"
    assert _prio({"cpu": ["i7-137"]}, W10_HYBRID) == "below_normal"
    assert _prio({"cpu": ["13400F"]}, UNKNOWN) == "below_normal"
    assert _prio({"cpu": ["known"]}, {**UNKNOWN, "cpu": ""}) == "below_normal"  # 读不到型号：什么片段都不中


def test_conditions_in_old_settings_are_skipped():
    """1.1.8 及以前不认条件、会对所有电脑生效，所以条件只能写在 settings_v2；写在 settings 里本版也不执行。"""
    s = Settings(install_id="0123456789abcdef")
    cloudsettings.apply(s, cfg({"id": "old", "versions": ["1.1.9"], "os": ["10"], "hybrid": True, **PRIO}), "1.1.9", W10_HYBRID)
    assert s.vision_priority == "below_normal"
    cloudsettings.apply(s, cfg({"id": "old-plain", **PRIO}), "1.1.9", W10_HYBRID)  # 老格式照旧
    assert s.vision_priority == "normal"


def test_old_clients_never_see_settings_v2():
    """1.1.5 起 _merge 就只留 DEFAULT 里有的顶层段：不认识的段整段丢掉。1.1.8 及以前没有 settings_v2，读不到。"""
    assert "zz_new" not in cloud._merge({"zz_new": [{"id": "x", **PRIO}]})
    merged = cloud._merge({"settings_v2": [{"id": "x", **PRIO}]})
    assert merged["settings"] == [] and merged["settings_v2"] == [{"id": "x", **PRIO}]


def test_ids_condition_needs_usage_stats():
    rule = {"ids": ["0123456789abcdef", "ffffffffffffffff"]}
    assert _prio(rule, UNKNOWN) == "normal"
    assert _prio(rule, UNKNOWN, install_id="1111111111111111") == "below_normal"
    assert _prio(rule, UNKNOWN, send_usage=False) == "below_normal"  # 关了使用统计没有编号


def test_bad_condition_values_never_match():
    for bad in ({"os": 10}, {"os": []}, {"os": ["10", "12"]}, {"hybrid": "true"}, {"vendor": [1]},
                {"vendor": ["intel", "other"]}, {"cpu": ""}, {"cpu": " "}, {"cpu": ["13400F", " "]}, {"ids": {}},
                {"ids": ["0123456789abcdef", "XYZ"]}, {"ids": ["0123456789ABCDEF"]},
                {"versions": "1.1.9"}, {"versions": []}, {"versions": None},
                {"percent": "50"}, {"percent": float("nan")}, {"percent": 101}, {"percent": True}):
        assert _prio(bad, W10_HYBRID) == "below_normal", bad


def test_no_id_computers_only_join_full_percent():
    assert _prio({"percent": 99}, W10_HYBRID, send_usage=False) == "below_normal"
    assert _prio({"percent": 99.5}, W10_HYBRID, send_usage=False) == "below_normal"  # 无编号固定桶 99，小数也不能进
    assert _prio({"percent": 99.99}, W10_HYBRID, send_usage=False) == "below_normal"
    assert _prio({"percent": 100}, W10_HYBRID, send_usage=False) == "normal"


def test_v2_rule_without_versions_is_skipped():
    """条件规则漏写 versions 会对以后所有版本生效：整条跳过，不当成「所有版本」。"""
    s = Settings(install_id="0123456789abcdef")
    for rule in ({"id": "no-ver", "os": ["10"], "hybrid": True}, {"id": "no-ver-ids", "ids": ["0123456789abcdef"]},
                 {"id": "no-ver-plain"}):
        cloudsettings.apply(s, v2({**rule, **PRIO}), "1.1.9", W10_HYBRID)
        assert s.vision_priority == "below_normal", rule
    assert cloudsettings._bad_v2({"os": ["10"]}).startswith("没写 versions")
    cloudsettings.apply(s, v2({"id": "ver", **V9, "os": ["10"], "hybrid": True, **PRIO}), "1.1.9", W10_HYBRID)
    assert s.vision_priority == "normal"
    cloudsettings.apply(s, v2({"id": "ver", **V9, "os": ["10"], "hybrid": True, **PRIO}), "1.2.0", W10_HYBRID)
    assert s.vision_priority == "below_normal"


def test_unknown_keys_skip_rule_but_underscore_is_comment():
    assert _prio({"ram_max": 8000}, W10_HYBRID) == "below_normal"  # 以后的新条件：本版整条跳过
    assert _prio({"_说明": "Win10 大小核试验"}, W10_HYBRID) == "normal"


def test_conditions_then_percent_split_within_group():
    hit = 0
    for i in range(200):
        s = Settings(install_id=f"{i:016x}")
        cloudsettings.apply(s, v2({"id": "w10-half", **V9, "os": ["10"], "hybrid": True, "percent": 50, **PRIO}), "1.1.9", W10_HYBRID)
        hit += s.vision_priority == "normal"
    assert 60 < hit < 140
    s = Settings(install_id="0000000000000000")
    cloudsettings.apply(s, v2({"id": "w10-all", **V9, "os": ["10"], "hybrid": True, "percent": 100, **PRIO}), "1.1.9", W11_AMD)
    assert s.vision_priority == "below_normal"


def test_removing_conditional_force_restores():
    s = Settings(install_id="0123456789abcdef")
    cloudsettings.apply(s, v2({"id": "w10", **V9, "os": ["10"], "hybrid": True, **PRIO}), "1.1.9", W10_HYBRID)
    assert s.vision_priority == "normal"
    res = cloudsettings.apply(s, v2(), "1.1.9", W10_HYBRID)
    assert s.vision_priority == "below_normal" and res["changed"] == ["vision_priority"]


def test_machine_read_only_when_a_rule_needs_it(monkeypatch):
    from lolhex import diagnostics
    calls = []
    monkeypatch.setattr(diagnostics, "machine", lambda: calls.append(1) or W10_HYBRID)
    s = Settings(install_id="0123456789abcdef")
    cloudsettings.apply(s, cfg({"id": "plain", **PRIO}), "1.1.9")
    cloudsettings.apply(s, v2({"id": "ids", **V9, "ids": ["0123456789abcdef"], **PRIO}), "1.1.9")
    assert calls == [] and s.vision_priority == "normal"
    cloudsettings.apply(s, v2({"id": "w10", **V9, "os": ["10"], **PRIO}), "1.1.9")
    assert calls == [1] and s.vision_priority == "normal"

    def broken():
        raise OSError("WMI 卡住了")
    monkeypatch.setattr(diagnostics, "machine", broken)
    cloudsettings.apply(s, v2({"id": "w10", **V9, "os": ["10"], **PRIO}), "1.1.9")  # 读不到：不中，也不报错
    assert s.vision_priority == "below_normal"


def test_machine_info_from_windows_facts(monkeypatch):
    from types import SimpleNamespace
    from lolhex import diagnostics
    monkeypatch.setattr(diagnostics, "_MACHINE", None)
    monkeypatch.setattr(diagnostics.os, "name", "nt")
    monkeypatch.setattr(diagnostics.sys, "getwindowsversion", lambda: SimpleNamespace(major=10, build=19045), raising=False)
    monkeypatch.setattr(diagnostics, "cpu_model", lambda: "13th Gen Intel(R) Core(TM) i7-13700F")
    monkeypatch.setattr(diagnostics, "core_classes", lambda: "P16+E8")
    assert diagnostics.machine() == {"os": "10", "hybrid": True, "vendor": "intel", "cpu": "13th Gen Intel(R) Core(TM) i7-13700F"}
    monkeypatch.setattr(diagnostics, "_MACHINE", None)
    monkeypatch.setattr(diagnostics.sys, "getwindowsversion", lambda: SimpleNamespace(major=10, build=26200), raising=False)
    monkeypatch.setattr(diagnostics, "cpu_model", lambda: "AMD Ryzen 5 5600 6-Core Processor")
    monkeypatch.setattr(diagnostics, "core_classes", lambda: "否")
    m = diagnostics.machine()
    assert m["os"] == "11" and m["hybrid"] is False and m["vendor"] == "amd"
    monkeypatch.setattr(diagnostics, "_MACHINE", None)
    monkeypatch.setattr(diagnostics, "cpu_model", lambda: "")  # 型号读不到：不拿 platform 的描述凑

    def broken():
        raise OSError("坏了")
    monkeypatch.setattr(diagnostics, "core_classes", broken)
    m = diagnostics.machine()
    assert m["cpu"] == "" and m["vendor"] == "" and m["hybrid"] is None and m["os"] == "11"
