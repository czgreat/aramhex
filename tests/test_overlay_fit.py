"""分数缩放下浮层摆不准：多出的 1 像素收回到目标范围内，之后不再每次刷新都重摆（无界面模式运行）。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def layer(monkeypatch):
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from lolhex.ui import overlay
    calls, state = [], {"rect": None}

    def window_rect(_hwnd):
        return state["rect"]

    def set_window_rect(_hwnd, r):
        calls.append(r)
        # 模拟 Qt 按 1.5 倍逻辑像素取整：宽高摆出来是 round(w/1.5)*1.5 向上取整
        w, h = round(r.width / 1.5), round(r.height / 1.5)
        state["rect"] = overlay.win.Rect(r.left, r.top, -(-w * 3 // 2), -(-h * 3 // 2))

    monkeypatch.setattr(overlay.win, "IS_WINDOWS", True)
    monkeypatch.setattr(overlay.win, "window_rect", window_rect)
    monkeypatch.setattr(overlay.win, "set_window_rect", set_window_rect)
    monkeypatch.setattr(overlay.win, "dpi_info", lambda *_: "")
    lay = overlay.BadgeLayer()
    lay.winId = lambda: 1
    state["rect"] = overlay.win.Rect(5, 5, 10, 10)
    yield lay, calls, state, overlay
    lay.hide()


def test_overflow_is_pulled_inside_and_not_repeated(layer):
    lay, calls, state, overlay = layer
    target = overlay.win.Rect(0, 0, 2560, 1600)
    lay._place(target)
    got = state["rect"]
    assert got.right <= target.right and got.bottom <= target.bottom  # 不再伸到相邻显示器
    assert target.width - got.width <= 2 and target.height - got.height <= 2
    n = len(calls)
    for _ in range(20):  # 后续刷新：位置没变就不再 SetWindowPos
        lay._place(target)
    assert len(calls) == n


def test_moved_window_is_placed_again(layer):
    lay, calls, state, overlay = layer
    target = overlay.win.Rect(0, 0, 2560, 1600)
    lay._place(target)
    n = len(calls)
    state["rect"] = overlay.win.Rect(100, 100, 50, 50)  # 被别的东西挪走了
    lay._place(target)
    assert len(calls) > n and state["rect"].left == 0
    other = overlay.win.Rect(300, 200, 1200, 600)  # 换了目标
    lay._place(other)
    assert state["rect"].left == 300 and state["rect"].right <= other.right


def test_exact_fit_is_untouched(layer):
    lay, calls, state, overlay = layer
    target = overlay.win.Rect(0, 0, 1920, 1080)
    state["rect"] = target
    lay._place(target)
    assert calls == []


def test_badges_repaint_only_when_content_or_place_changes(layer, monkeypatch):
    lay, calls, state, overlay = layer
    painted = []
    monkeypatch.setattr(lay, "update", lambda *args: painted.append(args))
    game = overlay.win.Rect(0, 0, 1920, 1080)
    state["rect"] = lay.band(game, [(500, 400, 700, 440)])  # 已摆在目标位置
    snap = {"evaluation": {"slots": [{"index": 0, "verdict": "best"}], "advice": "a"},
            "offer": {"boxes": [(500, 400, 700, 440)], "at": 1.0},
            "settings": {"show_winrate": True, "badge_position": "top"}}
    import copy
    for _ in range(8):  # 250 毫秒一次的刷新，内容一样（每次都是新拷贝）
        lay.update_from(copy.deepcopy(snap), game, 1)
    assert len(painted) == 1
    changed = copy.deepcopy(snap)
    changed["evaluation"]["advice"] = "b"
    lay.update_from(changed, game, 1)
    assert len(painted) == 1  # 总推荐语没有画在卡片标签里，不应触发三张重画。
    changed["evaluation"]["slots"][0]["verdict"] = "ok"
    lay.update_from(changed, game, 1)
    assert len(painted) == 2
    changed["settings"]["badge_position"] = "bottom"
    lay.update_from(copy.deepcopy(changed), game, 1)
    assert len(painted) == 3
    lay.hide()
    lay.update_from(copy.deepcopy(changed), game, 1)  # 隐藏后再出现要重画
    assert len(painted) == 4


@pytest.mark.parametrize("scale", [1, 1.25, 1.5])
def test_middle_reroll_and_best_change_leave_right_badge_untouched(layer, monkeypatch, scale):
    import copy
    lay, _, state, overlay = layer
    monkeypatch.setattr(lay, "_scale", lambda: scale)
    updates = []
    monkeypatch.setattr(lay, "update", lambda *args: updates.append(args))
    game = overlay.win.Rect(0, 0, 1920, 1080)
    boxes = [(440, 420, 750, 470), (810, 420, 1120, 470), (1180, 420, 1490, 470)]
    state["rect"] = lay.band(game, boxes)
    snap = {"evaluation": {"slots": [
        {"index": 0, "verdict": "best", "score": .8},
        {"index": 1, "verdict": "reroll", "score": -2.4},
        {"index": 2, "verdict": "reroll", "score": -4.0}]},
        "offer": {"boxes": boxes, "padded": True}, "settings": {}}
    lay.update_from(snap, game)
    right = lay._painted[1][2][1]
    left = lay._painted[1][0][1]
    changed = copy.deepcopy(snap)
    changed["evaluation"]["slots"][0]["verdict"] = "reroll"
    changed["evaluation"]["slots"][1].update(verdict="best", score=3.9)
    lay.update_from(changed, game)
    assert len(updates) == 2 and updates[0] == ()
    dirty, = updates[1]
    assert dirty.intersects(left)
    assert dirty.intersects(lay._painted[1][1][1])
    assert not dirty.intersects(right)
    lay.update_from(copy.deepcopy(changed), game)
    assert len(updates) == 2


def test_dirty_region_clears_old_equipment_hint_when_removed(layer, monkeypatch):
    import copy
    lay, _, state, overlay = layer
    updates = []
    monkeypatch.setattr(lay, "update", lambda *args: updates.append(args))
    game = overlay.win.Rect(0, 0, 1920, 1080)
    box = (500, 400, 700, 440)
    state["rect"] = lay.band(game, [box])
    snap = {"evaluation": {"slots": [{"index": 0, "verdict": "best", "item_hint": {
        "source": "stats", "items": [{"item": "3031", "name": "无尽之刃", "games": 1000, "win": .6}]}}]},
        "offer": {"boxes": [box]}, "settings": {}}
    lay.update_from(snap, game)
    previous = lay._painted[1][0][1]
    changed = copy.deepcopy(snap)
    changed["evaluation"]["slots"][0].pop("item_hint")
    lay.update_from(changed, game)
    dirty, = updates[1]
    assert previous.subtracted(dirty).isEmpty()
