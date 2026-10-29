"""标题条读卡：名字完全对上时颜色冲突也信文字（Gitea #36）；读的时候被别的线程清空不报错（Gitea #37）。"""
import numpy as np

import lolhex.vision.cardframe as cardframe
from lolhex.vision.detector import AugmentDetector
from lolhex.vision.matcher import NameMatcher
from lolhex.vision.window import Rect

NAMES = {"2078": "连锁反应", "1005": "喂呜喂呜", "1420": "咏叹奏鸣", "1001": "泰坦的坚决"}


def _detector(monkeypatch, values, colors, rarities=None, names=NAMES):
    class OCR:
        def __init__(self):
            self.values, self.calls, self.hook = list(values), 0, None

        def recognize(self, image):
            self.calls += 1
            if self.hook:
                self.hook(self.calls)
            return self.values.pop(0), 1.0
    ocr = OCR()
    monkeypatch.setattr(cardframe, "classify_card_frames", lambda *a, **k: list(colors))
    det = AugmentDetector(NameMatcher(names), ocr=ocr, rarities=rarities or
                          {"2078": "prismatic", "1005": "gold", "1420": "gold", "1001": "silver"})
    rect = Rect(0, 0, 1920, 1080)
    det.ensure_calibration(1920, 1080)
    sr = det.strip_rect(rect)
    return det, ocr, rect, np.zeros((sr.height, sr.width, 3), np.uint8)


def test_exact_name_beats_wrong_rarity(monkeypatch, caplog):
    # 资料把 2078 标成棱彩，卡框是金色：名字一字不差，照样认
    det, _, rect, strip = _detector(monkeypatch, ["连锁反应", "喂呜喂呜", "咏叹奏鸣"], ["gold"] * 3)
    with caplog.at_level("INFO"):
        assert det.check_strip(strip, rect).slots == ["2078", "1005", "1420"]
    assert det.cost["color_trust"] == 1 and det.cost["color_reject"] == 0
    assert "aid=2078" in caplog.text and "看到=gold" in caplog.text


def test_fuzzy_name_still_rejected_by_color(monkeypatch):
    # 云控 color_check=reject：错一个字（不是 100 分）且颜色不对，仍按颜色拒绝
    det, _, rect, strip = _detector(monkeypatch, ["泰坦的坚绝", "喂呜喂呜", "咏叹奏鸣"], ["gold"] * 3)
    det.color_check = "reject"
    assert det.check_strip(strip, rect).slots == [None, "1005", "1420"]
    assert det.cost["color_reject"] == 1 and det.cost["color_trust"] == 0


def test_conflict_logged_once(monkeypatch, caplog):
    det, _, rect, strip = _detector(monkeypatch, ["连锁反应", "喂呜喂呜", "咏叹奏鸣"] * 2, ["gold"] * 3)
    with caplog.at_level("INFO"):
        det.check_strip(strip, rect)
        det.clear_tracking()
        det.check_strip(strip, rect)
    assert det.cost["color_trust"] == 2 and caplog.text.count("aid=2078") == 1


def test_clear_tracking_during_read(monkeypatch):
    # 读第二列时别的线程 clear_tracking：不报 TypeError，这一轮作废，下一轮正常
    det, ocr, rect, strip = _detector(monkeypatch, ["连锁反应", "喂呜喂呜", "咏叹奏鸣"] * 2, [None] * 3)
    ocr.hook = lambda n: det.clear_tracking() if n == 2 else None
    assert det.check_strip(strip, rect) is None
    assert det._cells == [None] * 3
    ocr.hook = None
    assert det.check_strip(strip, rect).slots == ["2078", "1005", "1420"]


def test_clear_while_reading_changed_column(monkeypatch):
    # 以前：只有第 1 列变了，读它时别的线程清空缓存，循环到第 2 列取 self._cells[1] 已是 None → old["text"] TypeError
    det, ocr, rect, strip = _detector(monkeypatch, ["连锁反应", "喂呜喂呜", "咏叹奏鸣", "泰坦的坚决"], [None] * 3)
    assert det.check_strip(strip, rect).slots == ["2078", "1005", "1420"]
    pb = det.calibration.pixel_boxes(rect.width, rect.height)
    sx = min(b[0] for b in pb)
    strip[:, pb[0][0]-sx:pb[0][2]-sx] = 200
    ocr.hook = lambda n: det.clear_tracking()
    assert det.check_strip(strip, rect) is None and ocr.calls == 4
    assert det._cells == [None] * 3


def test_partial_ratio_100_still_rejected_by_color(monkeypatch):
    # 「棱彩阶恶趣味」对「恶趣味」partial_ratio 也是 100 分，但不是整个名字对上：颜色不对仍拒绝（#42 复核）
    det, _, rect, strip = _detector(monkeypatch, ["棱彩阶恶趣味", "喂呜喂呜", "咏叹奏鸣"], ["gold"] * 3,
                                    rarities={"1042": "silver", "1005": "gold", "1420": "gold"},
                                    names=dict(NAMES, **{"1042": "恶趣味"}))
    det.color_check = "reject"
    assert det.matcher.match("棱彩阶恶趣味") == ("1042", 100.0) and det.matcher.exact("棱彩阶恶趣味") is None
    assert det.check_strip(strip, rect).slots == [None, "1005", "1420"]
    assert det.cost["color_reject"] == 1 and det.cost["color_trust"] == 0


def test_clear_after_check_waits_for_commit(monkeypatch):
    # 写回检查通过后另一个线程 clear_tracking：要等这一轮写完再清，清完不能再冒出旧卡（#42 复核）
    import threading
    import lolhex.vision.detector as module
    det, _, rect, strip = _detector(monkeypatch, ["连锁反应", "喂呜喂呜", "咏叹奏鸣"], [None] * 3)
    real, workers = module.Offer, []

    def offer_then_clear(*a, **k):
        t = threading.Thread(target=det.clear_tracking)
        t.start()
        t.join(0.2)  # 拿不到锁会卡住；没加锁时这里就清完了
        workers.append(t)
        return real(*a, **k)
    monkeypatch.setattr(module, "Offer", offer_then_clear)
    det.check_strip(strip, rect)
    workers[0].join(2)
    assert not workers[0].is_alive()
    assert det._cells == [None] * 3 and det._last_result is None and det._stable is None


def test_default_color_mismatch_only_logged(monkeypatch, caplog):
    # 1.1.3 默认 color_check=log：名字有一个字读错、颜色也对不上，照样按名字认，记一条日志（带读到的字）
    det, _, rect, strip = _detector(monkeypatch, ["泰坦的坚绝", "喂呜喂呜", "咏叹奏鸣"], ["gold"] * 3)
    assert det.color_check == "log"
    with caplog.at_level("INFO"):
        assert det.check_strip(strip, rect).slots == ["1001", "1005", "1420"]
    assert det.cost["color_trust"] == 1 and det.cost["color_reject"] == 0
    assert "aid=1001" in caplog.text and "泰坦的坚绝" in caplog.text and "名字一字不差=False" in caplog.text


def test_color_check_off_skips_frame_classification(monkeypatch):
    det, _, rect, strip = _detector(monkeypatch, ["连锁反应", "喂呜喂呜", "咏叹奏鸣"], ["gold"] * 3)
    det.color_check = "off"
    monkeypatch.setattr(cardframe, "classify_card_frames", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该看颜色")))
    assert det.check_strip(strip, rect).slots == ["2078", "1005", "1420"]
    assert det.cost["color_trust"] == det.cost["color_reject"] == 0


def test_color_check_cloud_setting():
    from lolhex import cloudsettings
    from lolhex.config import Settings
    s = Settings()
    assert s.color_check == "log"
    cloudsettings.apply(s, {"settings": [{"id": "c", "force": {"color_check": "reject"}}]}, "1.1.3")
    assert s.color_check == "reject"
    cloudsettings.apply(s, {"settings": [{"id": "c", "force": {"color_check": "maybe"}}]}, "1.1.3")
    assert s.color_check == "log"  # 不认的值忽略；上次的强制撤掉，回到默认


def test_switching_color_check_rereads_static_strip(monkeypatch):
    # 画面不动时换处置方式：缓存是按旧方式判的，要重读，不能一直沿用 reject 的空结果（#47 复核）
    two_bad = ["泰坦的坚绝", "连锁反映", "喂呜喂呜"]
    for after in ("log", "off"):
        det, ocr, rect, strip = _detector(monkeypatch, two_bad * 2, ["gold"] * 3)
        det.color_check = "reject"
        assert det.check_strip(strip, rect) is None and ocr.calls == 3
        assert det.check_strip(strip, rect) is None and ocr.calls == 3  # 没换方式：走缓存
        det.color_check = after
        assert det.check_strip(strip, rect).slots == ["1001", "2078", "1005"] and ocr.calls == 6


def test_switching_to_reject_rejudges_without_flicker(monkeypatch, caplog):
    det, ocr, rect, strip = _detector(monkeypatch, ["泰坦的坚绝", "连锁反映", "喂呜喂呜"] * 2, ["gold"] * 3)
    with caplog.at_level("INFO"):
        assert det.check_strip(strip, rect).slots == ["1001", "2078", "1005"]
        stable = det._stable
        det.color_check = "log"  # 同样的值：不作废缓存
        assert det.check_strip(strip, rect).slots == ["1001", "2078", "1005"] and ocr.calls == 3
        det.color_check = "reject"
        assert det._stable is stable and det._last_result is not None  # 不清已显示的卡
        det.check_strip(strip, rect)
    assert ocr.calls == 6 and det.cost["color_reject"] == 2
    assert caplog.text.count("aid=1001") == 2  # 换了处置方式再记一条，日志能看出切换后的结果
