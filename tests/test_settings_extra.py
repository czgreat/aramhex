"""识别频率档位；浮窗不再对截图隐身，识别截图涂掉自己画的地方。"""
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


def test_old_allow_capture_setting_dropped(tmp_path):
    # 1.1.1（A1）：「录屏、截图时显示浮窗」删了（浮窗本来就能录到）；老设置文件里有也不报错、不写回
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"allow_capture": True, "speed": "low"}), encoding="utf-8")
    s = Settings.load(p)
    assert not hasattr(s, "allow_capture") and s.speed == "low"
    s.save(p)
    assert "allow_capture" not in json.loads(p.read_text(encoding="utf-8"))


def test_no_display_affinity_anywhere():
    # 浮窗应能被正常截图，识别时通过遮挡本程序绘制区域避免读到自己的文字。
    import pathlib
    import lolhex
    root = pathlib.Path(lolhex.__file__).parent
    hits = [p.name for p in root.rglob("*.py")
            if "SetWindowDisplayAffinity(" in p.read_text(encoding="utf-8").replace("不再调 SetWindowDisplayAffinity", "")]
    assert not hits


def test_mask_drawn_regions():
    import numpy as np
    from lolhex.vision import capture
    img = np.full((100, 200, 3), 200, np.uint8)
    rect = window.Rect(1000, 500, 200, 100)
    drawn = [window.Rect(1150, 480, 100, 40),   # 右上角露出一块：只涂重叠部分
             window.Rect(0, 0, 50, 50)]         # 不在截图里：不管
    assert capture.mask_drawn(img, rect, drawn) == 1
    assert (img[0:20, 150:200] == 0).all() and (img[20:, :] == 200).all() and (img[:, :150] == 200).all()


def test_drawn_registry_lingers_after_hide(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(window.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(window, "_drawn", {})
    r = window.Rect(1, 2, 3, 4)
    window.set_drawn("A", [r])
    window.set_drawn("A", [])          # 刚收起：屏幕上可能还没消失，多涂一会儿
    assert window.drawn_rects() == [r]
    clock[0] += window.DRAWN_LINGER_S + 0.1
    assert window.drawn_rects() == []
    window.set_drawn("B", [])          # 没登记过的收起：什么都不留
    assert window.drawn_rects() == []


def test_load_config_with_bom(tmp_path):
    """手改 config.json 后存成带 BOM 的 UTF-8：照样读，不能当成没有设置、被默认值覆盖。"""
    p = tmp_path / "config.json"
    p.write_bytes(b"\xef\xbb\xbf" + '{"game_lang": "ko_KR", "send_usage": false, "install_id": "0123456789abcdef"}'.encode())
    s = Settings.load(p)
    assert s.game_lang == "zh_CN" and s.send_usage is False  # 韩服 1.1.4 起不支持，按中文 and s.install_id == "0123456789abcdef"


def test_unwritable_or_broken_config_never_crashes(tmp_path, monkeypatch):
    # 设置文件写不进去：照常用（老用户第一次开 1.1.0 要写编号，写不进去也不能每次启动都崩）
    p = tmp_path / "config.json"
    p.write_text('{"speed": "low"}', encoding="utf-8")
    real = type(p).write_text

    def deny(self, *a, **k):
        if self.name.startswith("config.json"):  # 设置文件和存设置时的临时文件都写不进（目录只读）
            raise PermissionError("只读")
        return real(self, *a, **k)
    monkeypatch.setattr(type(p), "write_text", deny)
    s = Settings.load(p)
    assert s.speed == "low" and len(s.install_id) == 16 and s.save(p) is False
    monkeypatch.setattr(type(p), "write_text", real)
    # 设置文件实在读不懂：原文件备份成 .broken，这次不发匿名编号（能从原文看出用户的选择就照它的）
    for bad, usage in (("{坏了", False), ('{"send_usage": true, 坏了', True), ("[1, 2]", False)):
        p.write_text(bad, encoding="utf-8")
        s = Settings.load(p)
        backup = p.with_name("config.json.broken")
        assert backup.read_text(encoding="utf-8") == bad and s.send_usage is usage and s.seen_welcome
        assert json.loads(p.read_text(encoding="utf-8"))["send_usage"] is usage  # 新文件照常写
        backup.unlink()


def test_hand_edited_config_common_mistakes(tmp_path):
    # 按 README 手改关掉匿名统计：老记事本存成 GBK、末尾多一个逗号，都要认出来，不能悄悄重新打开
    p = tmp_path / "config.json"
    p.write_bytes('{"riot_id": "提莫#12345", "send_usage": false, "install_id": "0123456789abcdef",}'.encode("gbk"))
    s = Settings.load(p)
    assert s.send_usage is False and s.riot_id == "提莫#12345" and s.install_id == "0123456789abcdef"
    assert not p.with_name("config.json.broken").exists()
    # 编号被改成别的类型：重新生成（不然请求头发不出去）
    p.write_text('{"install_id": 123, "send_usage": "false"}', encoding="utf-8")
    s = Settings.load(p)
    assert len(s.install_id) == 16 and s.install_id != "123" and s.send_usage is False


def test_broken_config_backups_kept_and_unknown_usage_off(tmp_path):
    p = tmp_path / "config.json"
    for bad in ("{坏了1", "{坏了2"):
        p.write_text(bad, encoding="utf-8")
        Settings.load(p)
    kept = sorted(x.read_text(encoding="utf-8") for x in tmp_path.glob("config.json.broken*"))
    assert kept == ["{坏了1", "{坏了2"]                      # 连续两次坏：两份备份都在
    for v, want in (("null", False), ('"off"', False), ('"yes"', True), ("0", False)):
        p.write_text('{"send_usage": %s}' % v, encoding="utf-8")
        assert Settings.load(p).send_usage is want             # 认不出的值当关了


def test_removed_liveclient_setting_dropped(tmp_path):
    """1.1.1 删了 2999 接口：老设置文件里的 use_liveclient 读到就丢，存回去也不再有。"""
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"use_liveclient": True, "speed": "low"}), encoding="utf-8")
    s = Settings.load(p)
    assert not hasattr(s, "use_liveclient") and "use_liveclient" not in s.extra and s.speed == "low"
    s.save(p)
    assert "use_liveclient" not in json.loads(p.read_text(encoding="utf-8"))


def test_show_winrate_always_on(tmp_path):
    # 1.1.1：设置页去掉「显示胜率数字」，以前关掉的也打开（不然再也打不开）
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"show_winrate": False, "overlay": False}), encoding="utf-8")
    s = Settings.load(p)
    assert s.show_winrate is True and s.overlay is False  # 浮窗还能在托盘菜单里开关，保留玩家的选择


def test_old_auto_screenshot_dropped(tmp_path):
    # 用户 10-22：自动截图从设置页去掉（只在 debug.txt 调试模式下截）。1.0.0/1.1.0 存过开着的，升级后不报错、不再截、不写回
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"auto_screenshot": True, "speed": "saver"}), encoding="utf-8")
    s = Settings.load(p)
    assert not hasattr(s, "auto_screenshot") and s.debug_mode is False and s.speed == "saver"
    s.save(p)
    assert "auto_screenshot" not in json.loads(p.read_text(encoding="utf-8"))
    assert not (tmp_path / "config.json.tmp").exists()  # 先写临时文件再换过去


def test_set_by_user_while_forced(tmp_path):
    # 复核 10-22：云控强制某项时，玩家在托盘、分段下拉框改了：记下玩家要的值，当前仍按强制，存文件存玩家的
    p = tmp_path / "config.json"
    s = Settings.load(p)
    s.forced = {"overlay": True}
    s.overlay = False                  # 云控强制关
    s.set_by_user("overlay", True)
    assert s.overlay is False and s.forced["overlay"] is True and "overlay" in s.user_set
    s.save(p)
    assert json.loads(p.read_text(encoding="utf-8"))["overlay"] is True
    s.set_by_user("speed", "low")      # 没被强制的：直接改
    assert s.speed == "low"


def test_concurrent_saves_never_corrupt(tmp_path):
    import threading
    p = tmp_path / "config.json"
    s = Settings.load(p)
    def worker(i):
        for j in range(50):
            with s._lock:
                s.extra[f"k{i}_{j}"] = j
            s.save(p)
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(json.loads(p.read_text(encoding="utf-8"))) > 200
