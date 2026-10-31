import pytest
from conftest import fixture

from lolhex.data.tencent import parse_kiwi_augments
from lolhex.vision.matcher import NameMatcher, normalize

AUGS = parse_kiwi_augments(fixture("tencent_kiwi_augments.json"))
NAMES = {k: v["name"] for k, v in AUGS.items()}


def test_normalize():
    assert normalize("质变：棱彩阶") == normalize("质变:棱彩阶") == "质变棱彩阶"


def test_matcher_tolerates_ocr_noise():
    m = NameMatcher(NAMES)
    assert m.match("虚幻武器")[0] == "1029"
    assert m.match("质变:棱彩阶")[0] == "1238"
    assert m.match("炽燃利忌")[0] == "2128"          # 一个字识别错
    assert m.match("获得1个随机强化符文。")[0] is None  # 描述文字不应命中
    assert m.match("A")[0] is None


rapidocr = pytest.importorskip("rapidocr")
synth = pytest.importorskip("synth")
if not synth.fonts_available():
    pytest.skip("没有中文字体，跳过合成画面测试", allow_module_level=True)


@pytest.fixture(scope="module")
def detector():
    from lolhex.vision.detector import AugmentDetector
    return AugmentDetector(NameMatcher(NAMES))


def names(offer):
    return [NAMES.get(s) if s else None for s in offer.slots]


@pytest.mark.parametrize("size", [(3840, 2160), (1920, 1080)])
def test_full_scan_calibrates_and_strip_tracks_reroll(detector, size):
    from lolhex.vision.window import Rect
    detector.calibration = None
    offer = detector.scan_full(synth.render_offer(["质变：棱彩阶", "炽燃利息", "虚幻武器"], size))
    assert names(offer) == ["质变：棱彩阶", "炽燃利息", "虚幻武器"]
    assert detector.calibrated_for(*size)

    win = Rect(0, 0, *size)
    sr = detector.strip_rect(win)
    frame = synth.render_offer(["泰坦的坚决", "炽燃利息", "虚幻武器"], size, seed=3)
    offer2 = detector.check_strip(frame[sr.top:sr.bottom, sr.left:sr.right], win)
    assert names(offer2) == names(offer)  # 首个变化读数暂不改变整组推荐。
    offer2 = detector.check_strip(frame[sr.top:sr.bottom, sr.left:sr.right], win)
    assert names(offer2) == ["泰坦的坚决", "炽燃利息", "虚幻武器"]

    empty = synth.render_offer([None, None, None], size, seed=4)
    assert detector.check_strip(empty[sr.top:sr.bottom, sr.left:sr.right], win) is None

    # 点击位置映射到卡片列
    b = detector.calibration.pixel_boxes(*size)[2]
    assert detector.column_of((b[0] + b[2]) // 2, b[3] + 50, win) == 2


def test_two_cards_are_placed_by_symmetry(detector):
    size = (3840, 2160)
    assert names(detector.scan_full(synth.render_offer(["炽燃利息", None, "虚幻武器"], size))) == ["炽燃利息", None, "虚幻武器"]
    assert names(detector.scan_full(synth.render_offer([None, "炽燃利息", "虚幻武器"], size))) == [None, "炽燃利息", "虚幻武器"]


def test_no_offer_on_plain_screen(detector):
    assert detector.scan_full(synth.render_offer([None, None, None], (3840, 2160), seed=9)) is None


@pytest.mark.parametrize("size", [(3840, 2160), (1920, 1080), (2560, 1600)])
def test_default_calibration_reads_first_offer(tmp_path, size):
    """没校准过也能直接走轻量检查；三张都对上后存成本机校准。"""
    from lolhex.vision.detector import AugmentDetector
    from lolhex.vision.window import Rect
    det = AugmentDetector(NameMatcher(NAMES), tmp_path / "calibration.json")
    det.ensure_calibration(*size)
    assert det.calibrated_for(*size) and not det.confirmed_for(*size)
    win = Rect(0, 0, *size)
    sr = det.strip_rect(win)
    frame = synth.render_offer(["质变：棱彩阶", "炽燃利息", "虚幻武器"], size)
    offer = det.check_strip(frame[sr.top:sr.bottom, sr.left:sr.right], win)
    assert names(offer) == ["质变：棱彩阶", "炽燃利息", "虚幻武器"]
    assert det.confirmed_for(*size)
    assert AugmentDetector(NameMatcher(NAMES), tmp_path / "calibration.json").confirmed_for(*size)


def test_ocr_uses_one_thread(monkeypatch):
    """认字引擎只用一个线程：默认会占满所有核，对局中和游戏抢 CPU（玩家反馈掉帧、卡顿）。"""
    import rapidocr
    from lolhex.vision.ocr import OCR
    seen = []
    monkeypatch.setattr(rapidocr, "RapidOCR", lambda params=None: seen.append(params))
    OCR()
    for p in seen:
        assert p["EngineConfig.onnxruntime.intra_op_num_threads"] == 1
        assert p["EngineConfig.onnxruntime.inter_op_num_threads"] == 1


def test_ocr_sessions_really_single_thread():
    """上面只看传进去的参数；参数名写错时 RapidOCR 不报错、静默用默认（占满所有核），所以再看真实会话。"""
    pytest.importorskip("rapidocr")
    from lolhex.vision.ocr import OCR
    ocr = OCR()
    # RapidOCR 3.10 loads models on first use. Exercise the product's actual path
    # before inspecting sessions; orientation classification is disabled in it.
    image = synth.render_offer(["炽燃利息", "虚幻武器", "泰坦的坚决"], (1920, 1080))
    assert ocr.detect(image)
    eng = ocr._engine
    for part in (eng.text_det, eng.text_rec):
        opts = part.session.session.get_session_options()
        assert opts.intra_op_num_threads == 1 and opts.inter_op_num_threads == 1


def test_vision_cost_logged_per_game(detector, caplog):
    from lolhex.engine import Engine
    from lolhex.vision.window import Rect
    size = (1920, 1080)
    detector.calibration = None
    detector._last_fp = None                          # 不受前面测试留下的画面指纹影响
    detector.cost = dict.fromkeys(detector.cost, 0)
    detector.scan_full(synth.render_offer(["炽燃利息", "虚幻武器", "泰坦的坚决"], size))
    win = Rect(0, 0, *size)
    sr = detector.strip_rect(win)
    strip = synth.render_offer(["炽燃利息", "虚幻武器", "泰坦的坚决"], size, seed=5)[sr.top:sr.bottom, sr.left:sr.right]
    detector.check_strip(strip, win)
    detector.check_strip(strip, win)                 # 画面没变：不再认字，不算次数
    assert detector.cost["full"] == 1 and detector.cost["strip"] == 1
    with caplog.at_level("INFO"):
        Engine._log_vision_cost(detector)
    assert "标题条 1 次" in caplog.text and "整块扫描 1 次" in caplog.text and "CPU" in caplog.text
    assert detector.cost["strip"] == 0 and detector.cost["full"] == 0
