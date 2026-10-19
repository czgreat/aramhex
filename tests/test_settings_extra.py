"""识别频率档位和"录屏、截图时显示浮窗"开关。"""
import json

from lolhex.config import SPEEDS, Settings
from lolhex.vision import window


def test_speed_factor(tmp_path):
    p = tmp_path / "config.json"
    s = Settings.load(p)
    assert s.speed == "standard" and s.speed_factor == 1.0
    p.write_text(json.dumps({"speed": "low"}), encoding="utf-8")
    assert Settings.load(p).speed_factor == SPEEDS["low"][1] == 4.0
    p.write_text(json.dumps({"speed": "不存在"}), encoding="utf-8")
    assert Settings.load(p).speed_factor == 1.0  # 认不出的档位按标准


def test_capture_toggle_applies_to_open_windows(monkeypatch):
    applied = []
    monkeypatch.setattr(window, "_apply_affinity", lambda h: applied.append((h, window._capture_allowed)))
    monkeypatch.setattr(window, "_overlay_windows", set())
    monkeypatch.delenv("LOLHEX_DEBUG_CAPTURABLE", raising=False)
    window.set_capture_allowed(False)
    window.exclude_from_capture(101)
    window.exclude_from_capture(202)
    assert applied == [(101, False), (202, False)]
    applied.clear()
    window.set_capture_allowed(True)  # 打开后，已经开着的浮窗立即能被截到
    assert sorted(applied) == [(101, True), (202, True)] and window.capture_allowed()
    window.set_capture_allowed(False)

def test_load_config_with_bom(tmp_path):
    """手改 config.json 后存成带 BOM 的 UTF-8：照样读，不能当成没有设置、被默认值覆盖。"""
    p = tmp_path / "config.json"
    p.write_bytes(b"\xef\xbb\xbf" + '{"game_lang": "ko_KR", "send_usage": false, "install_id": "abc"}'.encode())
    s = Settings.load(p)
    assert s.game_lang == "ko_KR" and s.send_usage is False and s.install_id == "abc"
