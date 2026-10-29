"""双屏选海克斯掉帧（10-24）：三选一标签层只盖标签那一块、碰不到屏幕边缘；没在选海克斯时可以慢扫（云控）。"""

import pytest

from conftest import render_in_game


@pytest.mark.parametrize("size", [(1920, 1080), (2560, 1440), (3840, 2160)])
def test_badge_layer_stays_off_screen_edges(size):
    from PySide6.QtWidgets import QApplication
    from lolhex.ui.overlay import BadgeLayer
    from lolhex.vision import window
    app = QApplication.instance() or QApplication([])
    w, h = size
    k = h / 1080
    game = window.Rect(0, 0, w, h)
    # 三张卡标题框（国服默认位置，按窗口比例）
    boxes = [[int(w * a), int(h * b), int(w * c), int(h * d)] for a, b, c, d in
             [(0.2309, 0.3869, 0.3923, 0.4335), (0.4231, 0.3869, 0.5845, 0.4335), (0.6152, 0.3869, 0.7767, 0.4335)]]
    layer = BadgeLayer()
    band = layer.band(game, boxes)
    edge = BadgeLayer.EDGE
    assert band.left >= edge and band.top >= edge and band.right <= w - edge and band.bottom <= h - edge
    assert band.width * band.height < w * h * 0.6  # 不再铺满整个画面
    slots = [{"index": i, "verdict": "best", "score": 2, "item_hint": {"source": "stats", "items": [
        {"item": "3135", "name": "虚空之杖", "win": .55, "games": 1000}]}} for i in range(3)]
    for position in ("top", "bottom"):
        layer.update_from({"settings": {"badge_position": position}, "offer": {"boxes": boxes, "padded": True},
                           "evaluation": {"slots": slots}}, game)
        app.processEvents()
        from PySide6.QtGui import QImage
        image = QImage(w, h, QImage.Format_ARGB32)
        image.fill(0)
        render_in_game(layer, image)
        drawn = window.drawn_rects()
        assert len(drawn) == 6
        # 标签和装备提示都在层里，没被裁掉
        assert all(band.left <= r.left and r.left + r.width <= band.right + 3 * k
                   and band.top - 3 <= r.top and r.top + r.height <= band.bottom + 3 for r in drawn), (position, band, drawn)
    layer.hide()


def test_band_falls_back_inside_game_when_boxes_missing():
    from lolhex.ui.overlay import BadgeLayer
    from lolhex.vision import window
    band = BadgeLayer.band(BadgeLayer.__new__(BadgeLayer), window.Rect(100, 50, 2560, 1440), [None, None, None])
    e = BadgeLayer.EDGE
    assert (band.left, band.top, band.right, band.bottom) == (100 + e, 50 + e, 2660 - e, 1490 - e)


def test_scan_slow_until_offer_then_fast_for_a_while(tmp_path):
    from lolhex.config import Settings
    from lolhex.engine import Engine
    engine = Engine.__new__(Engine)
    engine.settings = Settings()
    hold = engine.settings.active_hold_s
    assert engine._strip_step(False, float("inf")) == pytest.approx(0.3)      # 默认不慢扫
    engine.settings.idle_scan_ms = 1000                                        # 云控调成平时 1 秒
    assert engine._strip_step(False, float("inf")) == pytest.approx(1.0)      # 平时 1 秒
    assert engine._strip_step(True, 0) == pytest.approx(0.3)                  # 三选一开着
    assert engine._strip_step(False, hold - 1) == pytest.approx(0.3)          # 刚关掉几秒内
    assert engine._strip_step(False, hold + 1) == pytest.approx(1.0)
    engine.settings.speed = "saver"
    assert engine._strip_step(False, hold + 1) == pytest.approx(2.0)          # 省电档照样翻倍
    engine.settings.speed = "standard"
    engine.settings.scan_interval_ms = 1500                                    # 快扫本来就比慢扫慢：不再拖慢
    assert engine._strip_step(False, hold + 1) == pytest.approx(1.5)


def test_fallback_full_scan_interval(tmp_path):
    """本机校准过、没在选海克斯：这局标题条还没认出过三选一时 30 秒一次（和 1.1.2 一样），
    认出过后基本不再整块扫描（3600 秒）；两项云控可调；没校准照旧按 bootstrap 快扫。"""
    from lolhex import cloudsettings
    from lolhex.config import Settings
    from lolhex.engine import Engine
    engine = Engine.__new__(Engine)
    engine.settings = Settings()
    assert engine._fallback_every(True) == pytest.approx(30)
    assert engine._fallback_every(True, proven=True) == pytest.approx(3600)
    assert engine._fallback_every(False) == pytest.approx(3.0)
    assert engine._fallback_every(False, proven=True) == pytest.approx(3.0)  # 默认位置还没确认：不放慢
    cfg = {"settings": [{"id": "fb", "defaults": {"fallback_scan_s": 60, "fallback_proven_s": 60}}]}
    cloudsettings.apply(engine.settings, cfg, "1.1.3")
    assert engine._fallback_every(True) == engine._fallback_every(True, proven=True) == pytest.approx(60)  # 不分段
    cloudsettings.apply(engine.settings, {"settings": [{"id": "fb-1", "force": {"fallback_scan_s": 1, "fallback_proven_s": 5}}]}, "1.1.3")
    assert engine.settings.fallback_scan_s >= 10 and engine.settings.fallback_proven_s >= 10  # 太小的不收
    engine.settings.fallback_proven_s = 10
    assert engine._fallback_every(True, proven=True) == pytest.approx(60)  # 认出过后不会比没认出时扫得更勤


@pytest.mark.parametrize("cloud_interval", [None, 600, 30])
def test_fallback_proven_skips_3599_seconds_and_resets_next_game(monkeypatch, cloud_interval):
    """默认认出后 3599 秒内不兜底；云控可退回 600 或 30 秒；下一局恢复 30 秒。"""
    from types import SimpleNamespace
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.vision import window
    from lolhex.vision.detector import Offer
    import lolhex.engine as module
    clock = [0.0]
    full, hits, size = [], [], {"rect": window.Rect(0, 0, 1920, 1080)}
    offer = Offer(["1", "2", "3"], ["a", "b", "c"], [100] * 3, [None] * 3)
    engine = Engine.__new__(Engine)

    def advance(delay):
        clock[0] += delay
        if 3600 <= clock[0] < 3601:
            engine.state["phase"] = "client"  # 对局结束
        elif 3610 <= clock[0] < 3612:
            engine.state["phase"] = "ingame"  # 下一局

    class Stop:
        def is_set(self):
            return clock[0] >= 5000

        def wait(self, delay):
            advance(delay)

    class Rescan:  # 对局外引擎在这上面等 1 秒：假时钟也要走
        def is_set(self):
            return False

        def clear(self):
            pass

        def wait(self, delay):
            advance(delay)

    def scan(det, rect, *, full=False, reason, **kw):
        if full:
            globals_full.append(clock[0])
            return None
        if 0.3 <= clock[0] < 0.6 or 3800 <= clock[0] < 3801:
            hits.append(clock[0])
            return offer
        return None
    globals_full = full
    det = SimpleNamespace(ensure_calibration=lambda *a: None, calibrated_for=lambda *a: True, confirmed_for=lambda *a: True)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(window, "find_game_window", lambda: 1)
    monkeypatch.setattr(window, "client_rect", lambda _: size["rect"])
    monkeypatch.setattr(window, "is_foreground", lambda _: True)
    monkeypatch.setattr(window, "lower_thread_priority", lambda: None)
    engine._stop, engine._rescan, engine._detector = Stop(), Rescan(), det
    engine.state = {"phase": "ingame", "vision": {}}
    engine.settings = Settings()
    if cloud_interval is not None:
        from lolhex import cloudsettings
        cloudsettings.apply(engine.settings, {"settings": [{"id": "fallback-rollback",
            "force": {"fallback_proven_s": cloud_interval}}]}, "1.1.3")
        assert engine.settings.fallback_proven_s == cloud_interval
    engine._observed_scan = scan
    engine._on_offer = engine._save_offer_shot = engine._log_vision_cost = lambda *a: None
    engine._scan_discarded = False
    engine._sync_augment_tables = lambda det: None  # 本测试只控制扫描节奏（#46 起每轮会同步名字表）
    engine._vision_loop()
    assert hits and hits[0] == pytest.approx(0.3)
    assert any(3800 <= t < 3801 for t in hits)  # 下一局也确实再次认出，不能靠没有命中误过测试
    proven = [t for t in full if hits[0] <= t < 3600]
    second = [t for t in full if 3610 <= t < 3800]
    after = [t for t in full if t >= 3800]
    if cloud_interval is None:
        assert not [t for t in full if hits[0] <= t <= hits[0] + 3599]
        assert not proven and not after
    else:
        assert len(proven) >= 5 and cloud_interval < proven[0] < cloud_interval + 2
        assert len(after) >= 2
        for scans in (proven, after):
            assert all(cloud_interval - 1 < b - a < cloud_interval + 2 for a, b in zip(scans, scans[1:]))
    assert len(second) >= 5 and all(29 < b - a < 32 for a, b in zip(second, second[1:]))  # 新的一局重新 30 秒


def test_fallback_proven_cleared_when_window_size_changes_and_back(monkeypatch):
    """A 尺寸认出三选一 → 换到 B 尺寸 → 换回 A：换回后没再认出前，兜底照 30 秒一次，不沿用旧的放慢。"""
    from types import SimpleNamespace
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.vision import window
    from lolhex.vision.detector import Offer
    import lolhex.engine as module
    clock = [0.0]
    full = []
    big, small = window.Rect(0, 0, 1920, 1080), window.Rect(0, 0, 1280, 720)
    offer = Offer(["1", "2", "3"], ["a", "b", "c"], [100] * 3, [None] * 3)
    engine = Engine.__new__(Engine)

    class Stop:
        def is_set(self):
            return clock[0] >= 800

        def wait(self, delay):
            clock[0] += delay

    class Rescan:
        def is_set(self):
            return False

        def clear(self):
            pass

        def wait(self, delay):
            clock[0] += delay

    def scan(det, rect, *, full=False, reason, **kw):
        if full:
            full_at.append((clock[0], rect.width))
            return None
        return offer if 100 <= clock[0] < 101 else None
    full_at = full
    det = SimpleNamespace(ensure_calibration=lambda *a: None, calibrated_for=lambda *a: True, confirmed_for=lambda *a: True)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(window, "find_game_window", lambda: 1)
    monkeypatch.setattr(window, "client_rect", lambda _: small if 300 <= clock[0] < 500 else big)
    monkeypatch.setattr(window, "is_foreground", lambda _: True)
    monkeypatch.setattr(window, "lower_thread_priority", lambda: None)
    engine._stop, engine._rescan, engine._detector = Stop(), Rescan(), det
    engine.state = {"phase": "ingame", "vision": {}}
    engine.settings = Settings()
    engine._observed_scan = scan
    engine._on_offer = engine._save_offer_shot = engine._log_vision_cost = lambda *a: None
    engine._scan_discarded = False
    engine._sync_augment_tables = lambda det: None  # 本测试只控制扫描节奏（#46 起每轮会同步名字表）
    engine._vision_loop()
    assert not [t for t, _ in full if 101 <= t < 300]                     # A 尺寸认出后放慢
    changed = [t for t, w in full if 300 <= t < 500 and w == 1280]
    assert len(changed) >= 5 and 300 <= changed[0] < 301
    assert all(29 < b - a < 32 for a, b in zip(changed, changed[1:]))      # 换到 B 恢复 30 秒
    back = [t for t, w in full if t >= 500 and w == 1920]
    assert len(back) >= 8 and all(29 < b - a < 32 for a, b in zip(back, back[1:]))  # 换回 A 重新 30 秒
