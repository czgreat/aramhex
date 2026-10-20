"""头像比对：用随机纹理当"英雄头像"，检查缩放、平移、圆形遮罩、变暗、JPEG 之后仍能认对。"""

import pytest

cv2 = pytest.importorskip("cv2")
import numpy as np

from lolhex.vision import champselect
from lolhex.vision.portraits import IconBank


def _icons(n=40, seed=0):
    rng = np.random.default_rng(seed)
    out = {}
    for i in range(n):
        base = rng.integers(0, 256, (6, 6, 3), dtype=np.uint8)
        out[str(i + 1)] = cv2.resize(base, (120, 120), interpolation=cv2.INTER_CUBIC)
    return out


def _distort(img, size, dx, dy, circle, dark):
    s = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    pad = size // 6
    canvas = np.full((size + 2 * pad, size + 2 * pad, 3), 20, np.uint8)
    canvas[pad:pad + size, pad:pad + size] = s
    cv2.rectangle(canvas, (pad, pad), (pad + size - 1, pad + size - 1), (60, 170, 200), 2)
    crop = canvas[pad + dy:pad + dy + size, pad + dx:pad + dx + size].copy()
    if circle:
        m = np.zeros(crop.shape[:2], np.uint8)
        cv2.circle(m, (size // 2, size // 2), size // 2, 255, -1)
        crop[m == 0] = 15
    if dark:
        crop = (crop * 0.6).astype(np.uint8)
    _, enc = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return cv2.imdecode(enc, cv2.IMREAD_COLOR)


def test_classify_distorted():
    icons = _icons()
    bank = IconBank(icons)
    rng = np.random.default_rng(1)
    wrong = 0
    for hid, img in icons.items():
        crop = _distort(img, int(rng.choice([40, 64, 96])), int(rng.integers(-2, 3)), int(rng.integers(-2, 3)),
                        bool(rng.integers(0, 2)), bool(rng.integers(0, 2)))
        m = bank.classify(crop)
        assert m.hero_id in (hid, None)
        wrong += m.hero_id != hid
    assert wrong <= 2


def test_blank_slot_not_matched():
    bank = IconBank(_icons())
    assert bank.classify(np.full((64, 64, 3), 30, np.uint8)).hero_id is None
    assert bank.classify(np.zeros((4, 4, 3), np.uint8)).hero_id is None


def test_champselect_read(monkeypatch):
    icons = _icons()
    bank = IconBank(icons)
    frame = np.full((900, 1600, 3), 25, np.uint8)
    layout = {"bench": [], "team": []}
    for i, hid in enumerate(["3", "7", "11"]):
        x, y = 500 + i * 70, 20
        frame[y:y + 56, x:x + 56] = cv2.resize(icons[hid], (56, 56), interpolation=cv2.INTER_AREA)
        layout["bench"].append((x / 1600, y / 900, (x + 56) / 1600, (y + 56) / 900))
    layout["bench"].append((1200 / 1600, 20 / 900, 1256 / 1600, 76 / 900))  # 空位
    monkeypatch.setattr(champselect, "LAYOUT", layout)
    got = champselect.read(frame, bank, (100, 50))
    assert [s["hero_id"] for s in got["bench"]] == ["3", "7", "11"]
    assert got["bench"][0]["box"][:2] == (600, 70)
    assert got["team"] == []


def test_team_uses_name_reader_and_gold_name(monkeypatch):
    """队友头像是皮肤图认不出时，用称号文字；金色称号那一行是自己。名字位置没字的空行不读也不比头像。"""
    bank = IconBank(_icons())
    frame = np.full((1080, 1920, 3), 25, np.uint8)
    frame[176 + 120:206 + 120, 200:300] = (230, 230, 230)  # 第 2 行称号：白字
    frame[176 + 240:206 + 240, 200:300] = (60, 190, 240)  # 第 3 行称号涂成金色（BGR）
    reads = iter(["5", "9"])
    got = champselect.read(frame, bank, (0, 0), lambda crop: next(reads))
    assert [(s["slot"], s["hero_id"]) for s in got["team"]] == [(1, "5"), (2, "9")]
    assert got["mine"] == "9"
    assert got["bench"] == []


def test_empty_team_row_not_matched_by_icon(monkeypatch):
    """自定义只有自己时，空行的背景碰巧像某个英雄头像（实测认成弗拉基米尔）：名字位置没字就不比头像。"""
    icons = _icons()
    bank = IconBank(icons)
    frame = np.full((1080, 1920, 3), 25, np.uint8)
    for i in (1, 4):
        x0, y0, x1, y1 = champselect.boxes("team", 1920, 1080)[i]
        frame[y0:y1, x0:x1] = cv2.resize(icons["7"], (x1 - x0, y1 - y0))
    frame[176 + 120:206 + 120, 200:300] = (230, 230, 230)  # 只有第 2 行有人（有名字）
    got = champselect.read(frame, bank, (0, 0), lambda crop: None)
    assert [(s["slot"], s["hero_id"]) for s in got["team"]] == [(1, "7")]


def test_second_reference_icon():
    """拳头换了新头像、腾讯 CDN 还是旧的（萨科等）：每个英雄可以有两张参考图，哪张像都认。"""
    old, new = _icons(seed=0), _icons(seed=5)
    crop = cv2.resize(new["3"], (64, 64), interpolation=cv2.INTER_AREA)
    assert IconBank(old).classify(crop).hero_id is None
    m = IconBank({hid: [old[hid], new[hid]] for hid in old}).classify(crop)
    assert m.hero_id == "3" and m.second < m.score - 0.1   # 第二名是别的英雄，不是自己的另一张图


def test_flat_slot_not_matched():
    """空位几乎是一块颜色（带一点噪点）：直接当空的，不拿去和头像比。"""
    bank = IconBank(_icons())
    rng = np.random.default_rng(3)
    slot = np.clip(28 + rng.normal(0, 4, (72, 72, 3)), 0, 255).astype(np.uint8)
    assert bank.classify(slot).hero_id is None and bank.classify(slot).score == 0.0


def test_client_icons_file_loads():
    """内置的拳头客户端头像能读出来，都是英雄（数字 ID 小于 10000）。"""
    from lolhex.vision.portraits import client_icons
    icons = client_icons()
    assert len(icons) >= 170 and all(int(k) < 10000 for k in icons)
    assert all(im.shape == (64, 64, 3) for im in icons.values())


def test_rank_grade_and_summary():
    from lolhex.engine import rank_grade
    assert [rank_grade(r, 100) for r in (1, 10, 11, 30, 31, 60, 61, 85, 86, 100)] == \
        ["S", "S", "A", "A", "B", "B", "C", "C", "D", "D"]
    assert rank_grade(None, 100) is None
    pytest.importorskip("PySide6")
    from lolhex.ui.overlay import champ_select_summary
    cs = {"mine": "1", "team": [{"hero_id": "1", "name": "韦鲁斯", "win": 0.47, "grade": "C"}],
          "bench": [{"hero_id": "2", "name": "奥拉夫", "win": 0.464, "grade": "C"},
                    {"hero_id": "3", "name": "霞", "win": 0.492, "grade": "B"}]}
    s = champ_select_summary(cs)
    assert s.index("霞") < s.index("奥拉夫")  # 按胜率排序
    assert "↑2.2" in s and "你：韦鲁斯" in s


def test_is_champ_select_marker():
    frame = np.zeros((1080, 1920, 3), np.uint8)
    seen = []

    def rec(text, score=0.99):
        def f(crop):
            seen.append(crop.shape)
            return text, score
        return f
    assert champselect.is_champ_select(frame, rec("可用"))
    assert seen[0][:2] == (36, 80)  # 读的是"可用"那一小块
    assert not champselect.is_champ_select(frame, rec("开始匹配"))
    assert not champselect.is_champ_select(frame, rec("可用", 0.3))


def test_read_cards_two_or_three():
    """开局英雄卡：读到"选择你的英雄"才识别；卡片位置按称号文字定位，二选一/三选一通用。"""
    from lolhex.vision.ocr import TextLine
    frame = np.zeros((1080, 1920, 3), np.uint8)
    names = {"沙漠皇帝": "268", "黑暗之女": "1"}
    lines = [TextLine("黑暗之女", 0.99, (789, 26, 914, 65)), TextLine("沙漠皇帝", 0.99, (142, 25, 266, 65)),
             TextLine("传说", 0.7, (400, 0, 500, 60))]
    cards = champselect.read_cards(frame, lambda c: ("选择你的英雄", 0.99), lambda c: lines, names.get, (10, 0))
    assert [c["hero_id"] for c in cards] == ["268", "1"]
    cx = (cards[0]["box"][0] + cards[0]["box"][2]) / 2
    assert abs(cx - (10 + 430 + 204)) <= 1
    assert champselect.read_cards(frame, lambda c: ("", 0.0), lambda c: lines, names.get) == []


def test_read_cards_redetects_when_one_span_misread(monkeypatch):
    """按段认字时一张卡读错（「狂厄蔷薇」读成「狂厄普薇」），另一张认对了：也要整条再认一遍，不能只出一张。"""
    from lolhex.vision.ocr import TextLine
    frame = np.zeros((1080, 1920, 3), np.uint8)
    names = {"暴怒骑士": "240", "狂厄蔷薇": "233"}
    monkeypatch.setattr(champselect, "text_spans", lambda band: [(100, 200), (500, 620)])

    def recognize(crop):
        if crop.shape[1] == 100:
            return "暴怒骑士", 0.99
        if crop.shape[1] == 120:
            return "狂厄普薇", 0.91
        return "选择你的英雄", 0.99
    lines = [TextLine("暴怒骑士", 1.0, (100, 20, 200, 60)), TextLine("狂厄蔷薇", 0.94, (500, 20, 620, 60))]
    cards = champselect.read_cards(frame, recognize, lambda c: lines, names.get)
    assert [c["hero_id"] for c in cards] == ["240", "233"]


def test_memo_reuses_until_crop_changes():
    calls = []
    memo = champselect.Memo()

    def fn(crop):
        calls.append(1)
        return int(crop.mean())

    a = np.full((40, 40, 3), 100, np.uint8)
    assert memo("k", a, fn) == 100
    assert memo("k", a.copy(), fn) == 100  # 第二次画面一样：算静止，再算一次并缓存
    a2 = a.copy()
    a2[0, 0] = 101  # 细微噪声：不重算
    memo("k", a2, fn)
    assert len(calls) == 2
    b = np.full((40, 40, 3), 160, np.uint8)  # 换了英雄：重算
    assert memo("k", b, fn) == 160
    assert memo("other", b, fn) == 160  # 不同位置分开缓存
    assert len(calls) == 4


def _name_box(strokes):
    """模拟我方一行的英雄名：深色底，每个字是几道浅色笔画。"""
    box = np.full((60, 284, 3), (30, 22, 18), np.uint8)
    for x, y, w, h in strokes:
        box[y:y + h, x:x + w] = (200, 225, 235)
    return box


def test_memo_sees_name_change_with_same_length():
    """同字数的两个名字，压成小方块后平均差很小，以前会被当成没变、一直显示旧英雄。"""
    old = _name_box([(10 + 34 * k, 14, 26, 4) for k in range(4)] + [(20 + 34 * k, 14, 4, 30) for k in range(4)])
    new = old.copy()
    new[14:44, 78:104] = (30, 22, 18)  # 只换第三个字
    new[28:32, 78:104] = (200, 225, 235)
    new[14:44, 89:93] = (200, 225, 235)
    memo = champselect.Memo()
    read = {old.tobytes(): "旧", new.tobytes(): "新"}
    fn = lambda crop: read[crop.tobytes()]
    for _ in range(3):
        assert memo("name", old, fn) == "旧"
    assert memo("name", new, fn) == "新"


def test_memo_does_not_freeze_fade_frame():
    """换英雄时名字淡出淡入：半透明那一帧读成旧英雄，不能被缓存成最终结果。"""
    old = _name_box([(10, 14, 120, 4), (10, 40, 120, 4)])
    new = _name_box([(10, 14, 4, 30), (60, 14, 4, 30), (110, 14, 4, 30)])
    # 快淡入完的一帧：和最终画面只差一点，按"没变"算，靠"连续两次一样才缓存"兜住
    fade = (old.astype(np.float32) * 0.05 + new.astype(np.float32) * 0.95).astype(np.uint8)
    memo = champselect.Memo()
    reads = iter(["旧", "旧", "旧（半透明）", "新", "新"])
    fn = lambda crop: next(reads)
    assert memo("name", old, fn) == "旧"
    assert memo("name", old, fn) == "旧"
    assert memo("name", fade, fn) == "旧（半透明）"
    assert memo("name", new, fn) == "新"
    assert memo("name", new, fn) == "新"
    assert memo("name", new, fn) == "新"  # 已静止：复用，不再调用


def test_text_spans_skips_thin_borders():
    band = np.full((90, 1060, 3), (60, 30, 40), np.uint8)
    band[:, 330:333] = 200  # 卡片边框细竖线
    for x in (140, 465, 790):  # 三段称号，字间有空隙
        for k in range(4):
            band[30:60, x + k * 32:x + k * 32 + 26] = (190, 210, 225)
    spans = champselect.text_spans(band)
    assert len(spans) == 3
    assert all(a < x < b for (a, b), x in zip(spans, (200, 525, 850)))


def test_clicking_own_panel_keeps_client_foreground(monkeypatch):
    """点右上角浮窗时前台变成浮窗，但客户端还在画面上：选人标注不能消失。"""
    from types import SimpleNamespace
    from lolhex.vision import window as win
    fg = {"h": 111}
    user32 = SimpleNamespace(GetForegroundWindow=lambda: fg["h"], IsIconic=lambda h: 0)
    monkeypatch.setattr(win, "IS_WINDOWS", True)
    monkeypatch.setattr(win, "ctypes", SimpleNamespace(windll=SimpleNamespace(user32=user32)))
    monkeypatch.setattr(win, "_companions", set())
    assert win.is_foreground(111)
    fg["h"] = 222  # 点了自己的浮窗
    assert not win.is_foreground(111)
    win.add_companion(222)
    assert win.is_foreground(111)
    fg["h"] = 333  # 切到别的程序：照常隐藏
    assert not win.is_foreground(111)


def test_memo_retries_unrecognized_until_found():
    """称号淡入时先认不出；画面只差一点（缩略图没超阈值）也要重认，不能一直用"认不出"的缓存。"""
    memo = champselect.Memo()
    results = iter([None, None, "268"])
    calls = []

    def fn(crop):
        calls.append(1)
        return next(results)

    a = np.full((40, 40, 3), 100, np.uint8)
    assert memo("k", a, fn, bool) is None
    assert memo("k", a.copy(), fn, bool) is None
    assert memo("k", a.copy(), fn, bool) == "268"
    assert memo("k", a.copy(), fn, bool) == "268"  # 认出来之后照常复用
    assert len(calls) == 3


def test_read_cards_not_stuck_after_fade_in():
    """先在淡入中途扫到（有标题、认不出名字），画面稳定后再扫一次就要出卡。"""
    from lolhex.vision.ocr import TextLine
    frame = np.zeros((1080, 1920, 3), np.uint8)
    names = {"沙漠皇帝": "268", "黑暗之女": "1"}
    memo = champselect.Memo()
    title = lambda c: ("选择你的英雄", 0.99)
    faded = [TextLine("沙漠", 0.4, (142, 25, 266, 65))]
    shown = [TextLine("黑暗之女", 0.99, (789, 26, 914, 65)), TextLine("沙漠皇帝", 0.99, (142, 25, 266, 65))]
    assert champselect.read_cards(frame, title, lambda c: faded, names.get, memo=memo) == []
    cards = champselect.read_cards(frame, title, lambda c: shown, names.get, memo=memo)
    assert [c["hero_id"] for c in cards] == ["268", "1"]
