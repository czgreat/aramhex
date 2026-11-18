"""1.1.8：识别节奏的新做法都能云控关掉、退回老做法。"""

import logging
from types import SimpleNamespace

from test_four_choice_engine import _loop

from lolhex import cloudsettings
from lolhex.config import Settings
from lolhex.diagnostics import cpu_set_classes, format_core_classes
from lolhex.vision.detector import Offer


class _Det(SimpleNamespace):
    """标题条一直只读到半截名字（商店价格、装备名等）。"""
    state = None

    @property
    def layout_suspect(self):
        return self.state["suspect"]

    def clear_layout_suspect(self):
        self.state["suspect"] = False


def _suspect_det(state):
    return _Det(state=state, ensure_calibration=lambda *a: None, calibrated_for=lambda *a: True,
                confirmed_for=lambda *a: True)


def _always_garbled(full, state, found_at=None):
    four = Offer(["1", "2", "3", "4"], [""] * 4, [100] * 4, [None] * 4)

    def scan(now, is_full, reason):
        if is_full:
            full.append((round(now, 2), reason))
            return None
        if found_at is not None and now >= found_at:
            return four
        state["suspect"] = True
        return None
    return scan


def test_half_name_rescans_stop_at_the_per_game_cap(monkeypatch):
    full, state = [], {"suspect": False}
    s = Settings()
    s.garbled_rescan_max = 3
    s.fallback_first_s = s.fallback_scan_s = 900  # 只看半截名字触发的
    _loop(monkeypatch, _always_garbled(full, state), until=120, settings=s, det=_suspect_det(state))
    assert [r for _, r in full] == ["garbled"] * 3


def test_half_name_rescans_off_is_116(monkeypatch):
    full, state = [], {"suspect": False}
    s = Settings()
    s.garbled_rescan_max = 0
    s.fallback_first_s = s.fallback_scan_s = 900
    _loop(monkeypatch, _always_garbled(full, state), until=60, settings=s, det=_suspect_det(state))
    assert not full


def test_no_half_name_rescan_after_the_first_offer_of_the_game(monkeypatch):
    """这局标题条认出过海克斯：布局已经对了，之后商店价格等读成半截名字也不再整块扫描。"""
    full, state = [], {"suspect": False}
    s = Settings()
    s.fallback_first_s = 900
    four = Offer(["1", "2", "3", "4"], [""] * 4, [100] * 4, [None] * 4)

    def scan(now, is_full, reason):
        if is_full:
            full.append((round(now, 2), reason))
            return None
        if 1 <= now < 2:
            return four
        state["suspect"] = now >= 10
        return None
    _loop(monkeypatch, scan, until=120, settings=s, det=_suspect_det(state))
    assert not [t for t, r in full if r == "garbled"]


def test_full_scans_spread_by_their_own_cost(monkeypatch):
    """慢电脑一次整块扫描 10 秒：下一次至少隔 40 秒；倍数写 0 退回 1.1.7 的每 5 秒。"""
    import lolhex.engine as module
    clock = {}

    def run(gap_x):
        full = []
        s = Settings()
        s.full_scan_gap_x = gap_x
        real = module.time.monotonic

        def scan(now, is_full, reason):
            if is_full:
                full.append(now)
                clock["advance"](10.0)  # 这次整块扫描花了 10 秒
            return None
        _loop_with_cost(monkeypatch, scan, 300, s, clock)
        module.time.monotonic = real
        return full
    slow = run(4.0)
    assert len(slow) >= 2 and all(b - a >= 10 + 40 - 0.01 for a, b in zip(slow, slow[1:]))
    fast = run(0)
    assert all(b - a < 16 for a, b in zip(fast, fast[1:]))


def _loop_with_cost(monkeypatch, scan, until, settings, clock):
    """和 _loop 一样，但扫描可以推进时钟（模拟耗时）。"""
    from lolhex.engine import Engine
    from lolhex.vision import window
    import lolhex.engine as module
    t = [0.0]
    clock["advance"] = lambda d: t.__setitem__(0, t[0] + d)

    class Stop:
        def is_set(self):
            return t[0] >= until

        def wait(self, delay):
            t[0] += delay

    class Rescan(Stop):
        def clear(self):
            pass

        def is_set(self):
            return False
    det = SimpleNamespace(ensure_calibration=lambda *a: None, calibrated_for=lambda *a: True,
                          confirmed_for=lambda *a: True)
    monkeypatch.setattr(module.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(window, "find_game_window", lambda: 1)
    monkeypatch.setattr(window, "client_rect", lambda _: window.Rect(0, 0, 1920, 1080))
    monkeypatch.setattr(window, "is_foreground", lambda _: True)
    monkeypatch.setattr(window, "set_vision_thread", lambda *a: False)
    engine = Engine.__new__(Engine)
    engine._stop, engine._rescan, engine._detector = Stop(), Rescan(), det
    engine.state = {"phase": "ingame", "vision": {}}
    engine.settings = settings
    engine._observed_scan = lambda det, rect, *, full=False, reason, **kw: scan(t[0], full, reason)
    engine._on_offer = engine._save_offer_shot = engine._log_vision_cost = lambda *a: None
    engine._scan_discarded = False
    engine._sync_augment_tables = lambda det: None
    engine._vision_loop()


def test_auto_idle_factor_zero_is_116():
    from lolhex.engine import Engine
    engine = Engine.__new__(Engine)
    engine.settings = Settings()
    engine._read_cpu_ms = 150.0
    assert engine.auto_idle_ms() == 750
    engine.settings.auto_idle_factor = 0
    assert engine.auto_idle_ms() == 0
    assert engine._strip_step(False, 999) == Settings.idle_scan_ms / 1000


def test_vision_thread_defaults_to_116_and_follows_cloud(monkeypatch, caplog):
    from lolhex.engine import Engine
    from lolhex.vision import window
    calls = []
    monkeypatch.setattr(window, "set_vision_thread", lambda p, e, n=False: calls.append((p, e, n)) or e)
    monkeypatch.setattr(window, "vision_thread_priority", lambda: None)  # 假装设上了（Windows 上会读到真实优先级）
    engine = Engine.__new__(Engine)
    engine.settings = Settings()
    with caplog.at_level(logging.INFO):
        engine._sync_vision_thread()
        engine._sync_vision_thread()  # 没变：不重设
        cloudsettings.apply(engine.settings, {"settings": [{"id": "eco-try", "force": {"vision_eco": True,
                                                                                     "vision_priority": "lowest"}}]}, "1.1.8")
        engine._sync_vision_thread()
        cloudsettings.apply(engine.settings, {"settings": [{"id": "no-throttle", "force": {"vision_no_throttle": True}}]},
                            "1.1.8")
        engine._sync_vision_thread()
    assert calls == [("below_normal", False, False), ("lowest", True, False), ("below_normal", False, True)]
    assert "优先级低于正常，效率模式关" in caplog.text and "优先级最低一档，效率模式已打开" in caplog.text
    assert "效率模式关（不让系统降频）" in caplog.text


def test_vision_thread_priority_failure_is_logged_and_retried(monkeypatch, caplog):
    """1.1.9：读回的优先级和要求的不一样，不记「优先级正常」，记一次没设上，下一轮再设。"""
    from lolhex.engine import Engine
    from lolhex.vision import window
    calls, actual = [], ["below_normal"]
    monkeypatch.setattr(window, "set_vision_thread", lambda p, e, n=False: calls.append(p) or False)
    monkeypatch.setattr(window, "vision_thread_priority", lambda: actual[0])
    engine = Engine.__new__(Engine)
    engine.settings = Settings()
    engine.settings.vision_priority = "normal"
    with caplog.at_level(logging.INFO):
        engine._sync_vision_thread()
        engine._sync_vision_thread()
        actual[0] = "normal"
        engine._sync_vision_thread()
        engine._sync_vision_thread()
    assert calls == ["normal"] * 3
    assert caplog.text.count("没设上（实际低于正常）") == 1
    assert caplog.text.count("识别线程：优先级正常，效率模式关") == 1


def test_cloud_accepts_the_new_items_and_rejects_bad_values():
    s = Settings()
    ok = {"garbled_rescan_s": 10, "garbled_rescan_max": 0, "full_scan_gap_x": 0, "auto_idle_factor": 0,
          "vision_priority": "lowest", "vision_eco": True, "vision_no_throttle": True}
    cloudsettings.apply(s, {"settings": [{"id": "a", "defaults": ok}]}, "1.1.8")
    assert {k: getattr(s, k) for k in ok} == ok
    t = Settings()
    cloudsettings.apply(s, {"settings": [{"id": "c", "defaults": {"vision_priority": "normal"}}]}, "1.1.8")
    assert s.vision_priority == "normal"  # 核少的电脑可以云控试正常优先级
    bad = {"garbled_rescan_max": 1.5, "full_scan_gap_x": -1, "vision_priority": "idle", "vision_eco": "yes",
           "auto_idle_factor": 99}
    cloudsettings.apply(t, {"settings": [{"id": "b", "defaults": bad}]}, "1.1.8")
    assert {k: getattr(t, k) for k in bad} == {k: getattr(Settings(), k) for k in bad}


def test_game_end_logs_full_scans_by_reason(caplog):
    from lolhex.engine import Engine
    engine = Engine.__new__(Engine)
    engine.settings = Settings()
    engine._full_stats = {"fallback": (3, 4.5), "garbled": (2, 30.0)}
    engine._garbled_skipped = {"已认出": 5}
    with caplog.at_level(logging.INFO):
        engine._log_full_scans()
        engine._log_full_scans()  # 记过就清零，不重复记
    assert caplog.text.count("这局整块扫描") == 1
    assert "fallback 3 次共 4.5 秒，garbled 2 次共 30.0 秒" in caplog.text and "已认出 5 次" in caplog.text


def _cpu_set(cls, kind=0, size=32):
    raw = bytearray(size)
    raw[0:4] = size.to_bytes(4, "little")
    raw[4:8] = kind.to_bytes(4, "little")
    raw[18] = cls
    return bytes(raw)


def test_core_classes_from_cpu_sets():
    hybrid = _cpu_set(1) * 16 + _cpu_set(0) * 8
    assert format_core_classes(cpu_set_classes(hybrid)) == "P16+E8"
    assert format_core_classes(cpu_set_classes(_cpu_set(0) * 16)) == "否"
    assert format_core_classes(cpu_set_classes(_cpu_set(0, kind=1) + b"\x00" * 3)) == ""
