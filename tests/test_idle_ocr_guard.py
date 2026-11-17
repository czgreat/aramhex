"""空闲省读字路径必须有界等待，并保留旧识别、换牌和升级行为。"""

import json

import numpy as np
import pytest

from lolhex import cloudsettings
from lolhex.config import Settings
from lolhex.vision.detector import AugmentDetector, Calibration
from lolhex.vision.matcher import NameMatcher
from lolhex.vision.title_probe import TitleSignal, probe_titles
from lolhex.vision.window import Rect


def pixels():
    boxes = [(0, 0, 96, 24), (104, 0, 200, 24), (208, 0, 304, 24)]
    return np.full((24, 304, 3), 30, np.uint8), boxes


def test_probe_requires_all_three_regions_and_only_returns_pixel_evidence():
    image, boxes = pixels()
    assert probe_titles(image, boxes, (0, 0)) is TitleSignal.EMPTY
    image[7:9, 140:144] = (140, 185, 210)
    assert probe_titles(image, boxes, (0, 0)) is TitleSignal.POSSIBLE
    translated = [tuple(v + (-300 if i % 2 == 0 else 70) for i, v in enumerate(b)) for b in boxes]
    assert probe_titles(image, translated, (-300, 70)) is TitleSignal.POSSIBLE


@pytest.mark.parametrize("kind", ["cropped", "missing", "tiny", "float", "gray", "bgra", "invalid"])
def test_unknown_input_never_means_no_cards(kind):
    image, boxes = pixels()
    if kind == "cropped": image = image[:, :-1]
    elif kind == "missing": boxes[2] = None
    elif kind == "tiny": boxes[1] = (104, 0, 110, 7)
    elif kind == "float": image = image.astype(float)
    elif kind == "gray": image = image[:, :, 0]
    elif kind == "bgra": image = np.zeros((24, 304, 4), np.uint8)
    elif kind == "invalid": boxes[2] = (208, 0, 305, 24)
    assert probe_titles(image, boxes, (0, 0)) is TitleSignal.UNKNOWN


def setup(monkeypatch):
    import lolhex.vision.detector as module
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    class OCR:
        calls = 0
        value = ""
        def recognize(self, image):
            self.calls += 1
            return self.value, 1
    ocr = OCR()
    det = AugmentDetector(NameMatcher({"1": "甲甲", "2": "乙乙", "3": "丙丙"}), ocr=ocr)
    det.calibration = Calibration.default(1920, 1080)
    det.calibration.guess = False
    det.idle_ocr_guard = True
    rect = Rect(-1920, 100, 1920, 1080)
    region = det.strip_rect(rect)
    strip = np.full((region.height, region.width, 3), 20, np.uint8)
    return det, ocr, clock, rect, strip


def test_initial_read_is_never_gated_and_dark_motion_reduces_calls(monkeypatch):
    det, ocr, clock, rect, strip = setup(monkeypatch)
    assert det.check_strip(strip, rect) is None and ocr.calls == 3
    for value in (35, 50, 65):
        clock[0] += .3
        strip[:] = value
        assert det.check_strip(strip, rect) is None
    assert ocr.calls == 3 and det.cost["idle_skipped"] == 3
    clock[0] += .3
    det.check_strip(strip, rect)
    assert ocr.calls == 6  # 正常300ms调度下，1秒后首轮强制回原路径。


def test_new_title_signal_bypasses_gate_on_the_next_tick(monkeypatch):
    det, ocr, clock, rect, strip = setup(monkeypatch)
    det.check_strip(strip, rect)
    clock[0] += .3
    strip[8:15, 20:80] = (140, 180, 210)
    det.check_strip(strip, rect)
    assert ocr.calls > 3 and det.cost.get("idle_skipped", 0) == 0


@pytest.mark.parametrize("state", ["visible", "pending", "group", "guess", "disabled"])
def test_active_or_unconfirmed_state_preserves_original_path(monkeypatch, state):
    det, ocr, clock, rect, strip = setup(monkeypatch)
    det.check_strip(strip, rect)
    clock[0] += .3
    strip[:] = 65
    if state == "visible": det._stable = object()
    elif state == "pending": det._pending[0] = {"aid": "1", "count": 1, "first": clock[0]}
    elif state == "group": det._group_target = [None] * 3
    elif state == "guess": det.calibration.guess = True
    else: det.idle_ocr_guard = False
    # 只记录原读字路径是否执行，避免测试桩改变稳定状态机。
    reads = []
    monkeypatch.setattr(det, "_read_strip", lambda *args: reads.append(True))
    det.check_strip(strip, rect)
    assert reads == [True] and det.cost.get("idle_skipped", 0) == 0


def test_failed_probe_falls_back_to_ocr(monkeypatch):
    import lolhex.vision.title_probe as module
    det, ocr, clock, rect, strip = setup(monkeypatch)
    det.check_strip(strip, rect)
    clock[0] += .3
    strip[:] = 65
    def fail(*args): raise RuntimeError("pixel failure")
    monkeypatch.setattr(module, "probe_titles", fail)
    det.check_strip(strip, rect)
    assert ocr.calls == 6 and det.cost["idle_probe_errors"] == 1


def test_tracking_reset_and_clock_reversal_force_fresh_read(monkeypatch):
    det, ocr, clock, rect, strip = setup(monkeypatch)
    det.check_strip(strip, rect)
    det.clear_tracking()
    clock[0] += .3
    det.check_strip(strip, rect)
    assert ocr.calls == 6
    clock[0] -= 1
    strip[:] = 65
    det.check_strip(strip, rect)
    assert ocr.calls == 9


def test_window_move_never_reuses_idle_skip(monkeypatch):
    det, ocr, clock, rect, strip = setup(monkeypatch)
    det.check_strip(strip, rect)
    clock[0] += .3
    moved = Rect(rect.left + 20, rect.top, rect.width, rect.height)
    det.check_strip(strip, moved)
    assert ocr.calls == 6


def test_idle_cooldown_starts_after_a_slow_read_finishes(monkeypatch):
    det, ocr, clock, rect, strip = setup(monkeypatch)
    original = ocr.recognize
    def slow(image):
        clock[0] += 1.0
        return original(image)
    monkeypatch.setattr(ocr, "recognize", slow)
    det.check_strip(strip, rect)
    assert clock[0] == 103.0
    clock[0] += .3
    strip[:] = 65
    det.check_strip(strip, rect)
    assert ocr.calls == 3 and det.cost["idle_skipped"] == 1


def test_legacy_config_and_calibration_are_preserved(tmp_path):
    config = tmp_path / "config.json"
    old = {"overlay_corner": "top-left", "badge_position": "bottom", "data_band": "mid",
           "install_id": "0123456789abcdef", "seen_welcome": True, "custom_legacy": {"x": 7}}
    config.write_text(json.dumps(old), encoding="utf-8")
    settings = Settings.load(config)
    assert settings.idle_ocr_guard is False
    settings.idle_ocr_guard = True
    assert settings.save(config)
    saved = json.loads(config.read_text(encoding="utf-8"))
    assert all(saved[key] == value for key, value in old.items())
    assert Settings.load(config).idle_ocr_guard is False  # 1.1.7 起设置页没有开关：存过的也改回关闭
    calibration = tmp_path / "calibration.json"
    calibration.write_text(json.dumps({"boxes": Calibration.default(1920, 1080).boxes,
                                       "size": [1920, 1080]}), encoding="utf-8")
    before = calibration.read_bytes()
    det = AugmentDetector(NameMatcher({}), calib_path=calibration)
    det.clear_tracking()
    det.ensure_calibration(1920, 1080)
    assert det.confirmed_for(1920, 1080) and calibration.read_bytes() == before


@pytest.mark.parametrize("value", [True, False, "true", "false", 1, None])
def test_guard_is_strictly_boolean(tmp_path, value):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"idle_ocr_guard": value}), encoding="utf-8")
    assert Settings.load(config).idle_ocr_guard is False  # 本机设置文件里的值一律不用，只认云控
    assert cloudsettings.SPEC["idle_ocr_guard"](value) is (value if type(value) is bool else None)


def test_cloud_force_and_revoke_do_not_persist_force(tmp_path):
    settings = Settings()
    cloudsettings.apply(settings, {"settings": [{"force": {"idle_ocr_guard": True}}]}, "1.1.6")
    assert settings.idle_ocr_guard is True
    config = tmp_path / "config.json"
    settings.save(config)
    assert Settings.load(config).idle_ocr_guard is False
    cloudsettings.apply(settings, {}, "1.1.6")
    assert settings.idle_ocr_guard is False


@pytest.mark.parametrize("size", [(1920, 1080), (3840, 2160)])
def test_real_ocr_recovers_from_idle_and_tracks_reroll(size):
    pytest.importorskip("rapidocr")
    import synth
    if not synth.fonts_available():
        pytest.skip("需要中文字体验证真实OCR")
    names = {"1": "炽燃利息", "2": "虚幻武器", "3": "质变：棱彩阶", "4": "泰坦的坚决"}
    det = AugmentDetector(NameMatcher(names))
    det.idle_ocr_guard = True
    det.calibration = Calibration.default(*size)
    det.calibration.guess = False
    rect = Rect(0, 0, *size)
    region = det.strip_rect(rect)
    def read(titles):
        image = synth.render_offer(titles, size)
        return det.check_strip(image[region.top:region.bottom, region.left:region.right], rect)
    assert read([None, None, None]) is None
    assert read([names["1"], names["2"], names["3"]]).slots == ["1", "2", "3"]
    assert read([names["4"], names["2"], names["3"]]).slots == ["1", "2", "3"]
    assert read([names["4"], names["2"], names["3"]]).slots == ["4", "2", "3"]
