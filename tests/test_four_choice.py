"""四选一（测试服地图）：卡距、卡宽、标题高度与三选一相同，整组以屏幕中心对称、错开半张。"""

import numpy as np
import pytest
from conftest import fixture

from lolhex.data.tencent import parse_kiwi_augments
from lolhex.vision.detector import AugmentDetector, Calibration, DEFAULT_BOXES, Offer
from lolhex.vision.matcher import NameMatcher
from lolhex.vision.ocr import TextLine
from lolhex.vision.window import Rect

AUGS = parse_kiwi_augments(fixture("tencent_kiwi_augments.json"))
NAMES = {k: v["name"] for k, v in AUGS.items()}
FOUR = ["质变：棱彩阶", "炽燃利息", "虚幻武器", "泰坦的坚决"]


def _line(text, cx, cy, H):
    h = 0.03 * H
    return (TextLine(text, 1.0, (int(cx - 60), int(cy - h / 2), int(cx + 60), int(cy + h / 2))), text, 100.0)


def _centers(W, H, offsets):
    s = (DEFAULT_BOXES[2][0] - DEFAULT_BOXES[0][0]) / 2 * 16 / 9 * H
    return [W / 2 + u * s for u in offsets]


def test_default_four_layout_is_three_layout_shifted_by_half_a_card():
    three = Calibration.default(1920, 1080)
    four = Calibration.default(1920, 1080, count=4)
    c3 = [(b[0] + b[2]) / 2 for b in three.boxes]
    c4 = [(b[0] + b[2]) / 2 for b in four.boxes]
    s = c3[1] - c3[0]
    assert four.count == 4 and four.valid() and four.guess
    assert np.allclose(np.diff(c4), s, atol=1e-3)                      # 卡距不变
    assert np.isclose(np.mean(c4), np.mean(c3), atol=1e-3)              # 仍以中线对称
    assert np.allclose([c4[1], c4[2]], [c3[1] - s / 2, c3[1] + s / 2], atol=1e-3)  # 中间两张错开半张
    assert {(b[1], b[3]) for b in four.boxes} == {(b[1], b[3]) for b in three.boxes}  # 标题高度不变
    # 实机截图（B 站视频画面）：卡距约 0.34 个窗口高度
    assert 0.33 < s * 1920 / 1080 < 0.35


@pytest.mark.parametrize("offsets, count, cols", [
    ([-1, 0, 1], 3, None),               # 三选一照旧
    ([-1.5, -0.5, 0.5, 1.5], 4, [0, 1, 2, 3]),
    ([-1.5, -0.5, 0.5], 4, [0, 1, 2]),  # 四选一漏认一张
    ([-0.5, 0.5, 1.5], 4, [1, 2, 3]),
    ([-1.5, -0.5, 1.5], 4, [0, 1, 3]),  # 中间漏一张：间距一倍、两倍
    ([-0.5, 0.5], 4, [1, 2]),
    ([-1.5, 1.5], 4, [0, 3]),
    ([-1, 1], 3, None),                  # 三选一左右两张：按原来的对称推断
    ([-1, 0], 3, None),
])
def test_layout_from_card_positions(offsets, count, cols):
    W, H = 1920, 1080
    group = [_line(f"卡{i}", cx, 0.41 * H, H) for i, cx in enumerate(_centers(W, H, offsets))]
    assert AugmentDetector._layout_columns(group, W, H) == (count, cols)


def test_pick_row_keeps_four_even_cards_but_not_uneven_extra_hits():
    W, H = 1920, 1080
    four = [_line(f"卡{i}", cx, 0.41 * H, H) for i, cx in enumerate(_centers(W, H, [-1.5, -0.5, 0.5, 1.5]))]
    assert len(AugmentDetector._pick_row(four, H)) == 4
    # 三张卡加一条别处的同行字：不等距，不当四选一，照旧取三条
    odd = [_line(f"卡{i}", cx, 0.41 * H, H) for i, cx in enumerate(_centers(W, H, [-1, 0, 1, 1.4]))]
    assert len(AugmentDetector._pick_row(odd, H)) == 3


def test_cloud_can_turn_four_choice_off(monkeypatch):
    """1.1.8：云控 features.four_choice=false 一律按三选一认（1.1.5 的认法）。"""
    from lolhex import cloud
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge({"features": {"four_choice": False}}))
    W, H = 1920, 1080
    four = [_line(f"卡{i}", cx, 0.41 * H, H) for i, cx in enumerate(_centers(W, H, [-1.5, -0.5, 0.5, 1.5]))]
    assert len(AugmentDetector._pick_row(four, H)) == 3
    assert AugmentDetector._layout_columns(four[:3], W, H) == (3, None)


def test_cloud_four_choice_off_drops_saved_and_live_four_layout(monkeypatch, tmp_path):
    """1.1.9：关了四选一，存下的和正在用的四选一位置都不用，换回三选一默认位置（重启和不重启都一样）。"""
    import json
    from lolhex import cloud
    W, H = 1920, 1080
    path = tmp_path / "calibration.json"
    four = Calibration.default(W, H, count=4)
    path.write_text(json.dumps({"boxes": four.boxes, "size": [W, H]}), encoding="utf-8")
    cfg = {"features": {}}
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge(cfg))
    det = AugmentDetector(NameMatcher(NAMES), calib_path=path)
    assert det.calibrated_for(W, H) and det._n() == 4
    cfg["features"]["four_choice"] = False  # 不重启
    assert not det.calibrated_for(W, H)
    det.ensure_calibration(W, H)
    assert det._n() == 3 and len(det._cells) == 3 and det.calibration.guess
    assert det.strip_rect(Rect(0, 0, W, H)) is not None
    det = AugmentDetector(NameMatcher(NAMES), calib_path=path)  # 重启
    assert det.calibration is None and path.exists()
    cfg["features"]["four_choice"] = True  # 再打开：存下的四选一照用
    assert AugmentDetector(NameMatcher(NAMES), calib_path=path)._n() == 4


def test_offer_pending_columns_follow_card_count():
    assert Offer(["1"] * 4, [""] * 4, [0] * 4, [None] * 4).pending_columns == (False,) * 4
    assert Offer(["1"] * 3, [""] * 3, [0] * 3, [None] * 3).pending_columns == (False,) * 3


def test_card_frames_and_title_probe_accept_four_boxes():
    from lolhex.vision.cardframe import classify_card_frames, frame_strip_bounds
    from lolhex.vision.title_probe import TitleSignal, probe_titles
    H = 1080
    pb = Calibration.default(1920, H, count=4).pixel_boxes(1920, H)
    bounds = frame_strip_bounds(pb, H)
    assert bounds and bounds[0] < pb[0][0] and bounds[2] > pb[3][2]
    strip = np.zeros((H, 1920, 3), np.uint8)
    assert classify_card_frames(strip, pb, strip_origin=(0, 0), window_height=H) == [None] * 4
    assert probe_titles(strip, pb, (0, 0)) is TitleSignal.EMPTY


rapidocr = pytest.importorskip("rapidocr")
synth = pytest.importorskip("synth")
needs_font = pytest.mark.skipif(not synth.fonts_available(), reason="没有中文字体，跳过合成画面测试")


def names(offer):
    return [NAMES.get(s) if s else None for s in offer.slots]


@needs_font
@pytest.mark.parametrize("size", [(1920, 1080), (2560, 1440), (3840, 2160)])
def test_full_scan_finds_four_cards_and_strip_tracks_them(size, tmp_path):
    det = AugmentDetector(NameMatcher(NAMES), calib_path=tmp_path / "calibration.json")
    offer = det.scan_full(synth.render_offer(FOUR, size))
    assert names(offer) == FOUR and offer.padded
    assert det.calibrated_for(*size) and det.calibration.count == 4
    win = Rect(0, 0, *size)
    sr = det.strip_rect(win)
    rerolled = ["质变：棱彩阶", "炽燃利息", "泰坦的坚决", "虚幻武器"]
    frame = synth.render_offer(rerolled, size, seed=3)
    for _ in range(3):  # 换卡要连读两次才采纳
        got = det.check_strip(frame[sr.top:sr.bottom, sr.left:sr.right], win)
    assert names(got) == rerolled
    # 四张的位置存下来，重启后照样用
    again = AugmentDetector(NameMatcher(NAMES), calib_path=tmp_path / "calibration.json")
    assert again.calibrated_for(*size) and again.calibration.count == 4


@needs_font
def test_switches_back_to_three_cards_on_the_next_map(tmp_path):
    size = (1920, 1080)
    det = AugmentDetector(NameMatcher(NAMES), calib_path=tmp_path / "calibration.json")
    assert det.scan_full(synth.render_offer(FOUR, size)) and det.calibration.count == 4
    three = ["炽燃利息", "虚幻武器", "泰坦的坚决"]
    win = Rect(0, 0, *size)
    sr = det.strip_rect(win)
    frame = synth.render_offer(three, size, seed=5)
    # 四张的标题框落在三张卡之间，只能读到半截名字：不能把它当成四选一的卡
    stale = det.check_strip(frame[sr.top:sr.bottom, sr.left:sr.right], win)
    assert stale is None or stale.matched() < 2
    offer = det.scan_full(frame)
    assert names(offer) == three and det.calibration.count == 3 and len(det._cells) == 3


@needs_font
def test_three_card_calibration_cannot_read_four_cards_until_full_scan(tmp_path):
    """上一局的三选一定位读四选一只能读到半截：标题条不会冒出错位的推荐，整块扫描后换成四张。"""
    size = (1920, 1080)
    det = AugmentDetector(NameMatcher(NAMES), calib_path=tmp_path / "calibration.json")
    assert det.scan_full(synth.render_offer(["炽燃利息", "虚幻武器", "泰坦的坚决"], size))
    win = Rect(0, 0, *size)
    sr = det.strip_rect(win)
    frame = synth.render_offer(FOUR, size, seed=7)
    stale = det.check_strip(frame[sr.top:sr.bottom, sr.left:sr.right], win)
    assert stale is None or stale.matched() < 2
    assert names(det.scan_full(frame)) == FOUR and det.calibration.count == 4


@needs_font
@pytest.mark.parametrize("size", [(1440, 1080), (1280, 1024)])
def test_narrow_screens_reach_the_outer_cards(size, tmp_path):
    """4:3、5:4 这类窄屏：四选一两边的卡贴近画面边缘，整块扫描要盖得到，定位框不能出界。"""
    det = AugmentDetector(NameMatcher(NAMES), calib_path=tmp_path / "calibration.json")
    frame = synth.render_offer(FOUR, size)
    offer = det.scan_full(frame)
    assert names(offer) == FOUR and det.calibration.count == 4 and det.calibration.valid()
    win = Rect(0, 0, *size)
    sr = det.strip_rect(win)
    det._last_fp = None
    assert names(det.check_strip(frame[sr.top:sr.bottom, sr.left:sr.right], win)) == FOUR


@needs_font
@pytest.mark.parametrize("old, new", [(["炽燃利息", "虚幻武器", "泰坦的坚决"], FOUR),
                                      (FOUR, ["炽燃利息", "虚幻武器", "泰坦的坚决"])])
def test_half_names_flag_a_layout_mismatch(old, new, tmp_path, monkeypatch):
    """定位和卡片数不符（上一局三选一、这局四选一，或反过来），标题条只读到半截名字：
    连续两次，或画面不动持续 1 秒，就要求马上整块扫描；读到正常的卡就清掉。"""
    from lolhex.vision import detector as module
    size = (1920, 1080)
    det = AugmentDetector(NameMatcher(NAMES), calib_path=tmp_path / "calibration.json")
    assert det.scan_full(synth.render_offer(old, size))
    det.clear_tracking()
    win = Rect(0, 0, *size)
    sr = det.strip_rect(win)
    frame = synth.render_offer(new, size, seed=7)
    strip = frame[sr.top:sr.bottom, sr.left:sr.right]
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    det.check_strip(strip, win)
    assert not det.layout_suspect
    det._last_fp, det._cells = None, [None] * len(det._cells)  # 卡片还在动：每列都重读
    det.check_strip(strip, win)
    assert det.layout_suspect
    det.clear_layout_suspect()
    det._last_fp, det._cells = None, [None] * len(det._cells)
    det.check_strip(strip, win)
    det.check_strip(strip, win)  # 卡片停稳了：用缓存，不再读
    clock[0] += module.GARBLED_HOLD_S / 2
    assert not det.layout_suspect
    clock[0] += module.GARBLED_HOLD_S / 2
    assert det.layout_suspect
    det.scan_full(frame)
    assert det.calibration.count == len(new)
    det._last_fp = None
    sr = det.strip_rect(win)
    det.check_strip(frame[sr.top:sr.bottom, sr.left:sr.right], win)
    assert not det.layout_suspect


@needs_font
def test_right_layout_never_flags_a_mismatch(tmp_path):
    size = (1920, 1080)
    for cards in (FOUR, ["炽燃利息", "虚幻武器", "泰坦的坚决"]):
        det = AugmentDetector(NameMatcher(NAMES), calib_path=tmp_path / f"c{len(cards)}.json")
        assert det.scan_full(synth.render_offer(cards, size))
        win = Rect(0, 0, *size)
        sr = det.strip_rect(win)
        for seed in range(3):
            frame = synth.render_offer(cards, size, seed=seed)
            det._last_fp = None
            det.check_strip(frame[sr.top:sr.bottom, sr.left:sr.right], win)
        assert not det.layout_suspect and det._garbled_reads == 0


THREE = ["炽燃利息", "虚幻武器", "泰坦的坚决"]


def _strip(det, frame, win):
    sr = det.strip_rect(win)
    return frame[sr.top:sr.bottom, sr.left:sr.right]


@needs_font
@pytest.mark.parametrize("switch", [True, False])
def test_partial_full_scan_after_four_choice_never_breaks_the_strip(switch, tmp_path, monkeypatch):
    """1.1.9 实录：上一局四选一，这局三选一，整块扫描只认出两张：张数改成 3 张、定位还是 4 张，
    标题条每读一次报一次错。1.2.0 张数和位置一起换；云控关掉新做法时也只是从头跟踪，不报错。"""
    from lolhex import cloud
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge({"features": {"count_switch": switch}}))
    size = (1920, 1080)
    win = Rect(0, 0, *size)
    det = AugmentDetector(NameMatcher(NAMES), calib_path=tmp_path / "calibration.json")
    assert det.scan_full(synth.render_offer(FOUR, size)) and det._n() == 4
    det.clear_tracking()
    partial = synth.render_offer([THREE[0], None, THREE[2]], size, seed=4)
    offer = det.scan_full(partial)
    assert len(offer.slots) == 3 and offer.matched() == 2
    assert det._n() == (3 if switch else 4)
    for _ in range(3):
        det._last_fp = None
        got = det.check_strip(_strip(det, partial, win), win)  # 1.1.9 这里 IndexError
    assert len(det._pending) == len(det._cells) == det._n()
    if switch:
        assert names(got) == [THREE[0], None, THREE[2]] and det.calibration.guess
        full = synth.render_offer(THREE, size, seed=6)
        for _ in range(3):
            det._last_fp = None
            got = det.check_strip(_strip(det, full, win), win)
        assert names(got) == THREE and not det.calibration.guess  # 认全了才存
        assert AugmentDetector(NameMatcher(NAMES), calib_path=tmp_path / "calibration.json")._n() == 3


def _garble(det, frame, win):
    for _ in range(2):
        det._last_fp, det._cells = None, [None] * len(det._cells)
        det.check_strip(_strip(det, frame, win), win)
    assert det.layout_suspect
    det.clear_layout_suspect()


@needs_font
@pytest.mark.parametrize("old, new", [(THREE, FOUR), (FOUR, THREE)])
def test_half_names_try_the_other_card_count_on_the_strip(old, new, tmp_path):
    """读到半截名字：先按另一种张数的位置读标题条，不等整块扫描；认全后存下新位置。"""
    size = (1920, 1080)
    win = Rect(0, 0, *size)
    det = AugmentDetector(NameMatcher(NAMES), calib_path=tmp_path / "calibration.json")
    assert det.scan_full(synth.render_offer(old, size))
    det.clear_tracking()
    frame = synth.render_offer(new, size, seed=7)
    _garble(det, frame, win)
    assert det.try_other_count(*size)
    assert det._n() == len(new) and det.calibration.guess and det.calibration.valid()
    for _ in range(3):
        det._last_fp = None
        got = det.check_strip(_strip(det, frame, win), win)
    assert names(got) == new and det._trial_from is None and not det.calibration.guess
    assert AugmentDetector(NameMatcher(NAMES), calib_path=tmp_path / "calibration.json")._n() == len(new)


@needs_font
def test_other_card_count_trial_goes_back_when_it_reads_nothing(tmp_path, monkeypatch):
    """试读的位置也读到半截名字，或几秒内没认出卡（商店里的字之类）：换回原来的位置，交给整块扫描。"""
    from lolhex.vision import detector as module
    size = (1920, 1080)
    win = Rect(0, 0, *size)
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    det = AugmentDetector(NameMatcher(NAMES), calib_path=tmp_path / "calibration.json")
    assert det.scan_full(synth.render_offer(THREE, size))
    saved = det.calibration
    assert det.try_other_count(*size) and det._n() == 4
    assert not det.try_other_count(*size)  # 又读到半截名字：换回，返回 False 走整块扫描
    assert det.calibration is saved and det._trial_from is None
    blank = synth.render_offer([None, None, None], size)
    assert det.try_other_count(*size)
    det.check_strip(_strip(det, blank, win), win)
    assert det._n() == 4
    clock[0] += module.TRIAL_S
    assert det.check_strip(_strip(det, blank, win), win) is None
    assert det.calibration is saved and len(det._cells) == 3
    assert det.try_other_count(*size)
    det.clear_tracking()  # 这局结束时还在试读：也换回
    assert det.calibration is saved


def test_other_card_count_trial_follows_cloud_switch(monkeypatch, tmp_path):
    from lolhex import cloud
    cfg = {"features": {"count_switch": False}}
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge(cfg))
    det = AugmentDetector(NameMatcher(NAMES), calib_path=tmp_path / "calibration.json")
    det.ensure_calibration(1920, 1080)
    assert not det.try_other_count(1920, 1080) and det._n() == 3
    cfg["features"] = {"four_choice": False}
    assert not det.try_other_count(1920, 1080) and det._n() == 3
    cfg["features"] = {}
    assert det.try_other_count(1920, 1080) and det._n() == 4


@pytest.mark.parametrize("size", [(1920, 1080), (1440, 1080), (1280, 1024), (3440, 1440)])
def test_switched_layout_stays_on_screen(size):
    from lolhex.vision.detector import _layout_boxes
    for count, other in ((3, 4), (4, 3)):
        base = Calibration.default(*size, count=count)
        boxes = _layout_boxes(base.boxes, other)
        c0 = [(b[0] + b[2]) / 2 for b in base.boxes]
        c1 = [(b[0] + b[2]) / 2 for b in boxes]
        assert Calibration(boxes, size).valid() and len(boxes) == other
        assert np.isclose(np.mean(c1), np.mean(c0), atol=1e-3) and np.allclose(np.diff(c1), c0[1] - c0[0], atol=1e-3)


def _saved(size, count):
    return Calibration(list(Calibration.default(*size, count=count).boxes), size)


def four_from(three):
    from lolhex.vision.detector import _layout_boxes
    return _layout_boxes(three.boxes, 4)


def test_calibration_file_keeps_both_card_counts(tmp_path):
    """1.2.0：calibration.json 三张、四张的位置各留一份；boxes 还是最近认出的那份（1.1.9 只读它）。"""
    import json
    path = tmp_path / "calibration.json"
    det = AugmentDetector(NameMatcher(NAMES), calib_path=path)
    det.calibration = _saved((1920, 1080), 3)
    det._save_calibration()
    det.calibration = _saved((1920, 1080), 4)
    det._save_calibration()
    d = json.loads(path.read_text(encoding="utf-8"))
    assert len(d["boxes"]) == 4 and sorted(d["layouts"]) == ["3", "4"]
    again = AugmentDetector(NameMatcher(NAMES), calib_path=path)
    assert again._n() == 4 and sorted(again._layouts) == [3, 4]
    assert again._layouts[3].boxes == det._layouts[3].boxes and not again._layouts[3].guess
    # 1.1.9 写的文件没有 layouts：照常读，只有那一份
    path.write_text(json.dumps({"boxes": d["layouts"]["3"]["boxes"], "size": [1920, 1080]}), encoding="utf-8")
    old = AugmentDetector(NameMatcher(NAMES), calib_path=path)
    assert old._n() == 3 and sorted(old._layouts) == [3]
    # layouts 里坏了一份：丢掉那份，别的照用
    d["layouts"]["3"]["boxes"] = [[0.9, 0.1, 0.1, 0.2]] * 3
    path.write_text(json.dumps(d), encoding="utf-8")
    assert sorted(AugmentDetector(NameMatcher(NAMES), calib_path=path)._layouts) == [4]


def test_alternate_between_saved_card_counts(tmp_path, monkeypatch):
    """这局还没认出：三张、四张的位置轮流读。认出过的用存的那份，没见过四选一按三张的卡距推算；
    推算的那份兜底整块扫描照确认过的间隔，换了窗口大小不轮流。"""
    from lolhex import cloud
    cfg = {"features": {}}
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge(cfg))
    size = (1920, 1080)
    det = AugmentDetector(NameMatcher(NAMES), calib_path=tmp_path / "calibration.json")
    det.calibration = three = _saved(size, 3)
    det._save_calibration()
    assert det.alternate_count(*size) and det._n() == 4 and det.calibration.guess  # 没见过四选一：推算
    assert det.confirmed_for(*size)
    assert [b[0] for b in det.calibration.boxes] == pytest.approx([b[0] for b in four_from(three)])
    assert det.alternate_count(*size) and det.calibration is three
    det.calibration = four = _saved(size, 4)
    det._save_calibration()
    assert det.alternate_count(*size) and det.calibration is three and len(det._cells) == 3
    assert det.alternate_count(*size) and det.calibration is four and len(det._pending) == 4
    assert not det.alternate_count(2560, 1440)  # 窗口大小不对
    assert det.try_other_count(*size) and det.calibration is three  # 读到半截名字时也直接用存的那份
    assert not det.alternate_count(*size)  # 正在试读时不轮流
    det.clear_tracking()
    for off in ("layout_memory", "count_switch", "four_choice"):
        cfg["features"] = {off: False}
        det.calibration = three
        assert not det.alternate_count(*size) and det.calibration is three
    cfg["features"] = {"layout_memory": False}
    assert det.try_other_count(*size) and det.calibration.guess  # 关掉后按卡距排，不用存的那份


def test_four_layout_from_three_matches_a_real_four_choice():
    """1.2.0-test.5 实录（3840x2160）：本机三张的位置推算出的四张，和后来整块扫描认出的四张差不到千分之一屏宽。"""
    from lolhex.vision.detector import _layout_boxes
    three = [(0.22725130208333333, 0.38708333333333333, 0.38863411458333336, 0.43374999999999997),
             (0.4190481770833333, 0.38708333333333333, 0.5804309895833334, 0.43374999999999997),
             (0.61149609375, 0.38708333333333333, 0.77287890625, 0.43374999999999997)]
    seen = [0.1313880208333333, 0.3231848958333333, 0.5149817708333333, 0.7070390624999999]
    assert [b[0] for b in _layout_boxes(three, 4)] == pytest.approx(seen, abs=0.001)
