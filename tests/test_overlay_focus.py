"""游戏内浮窗：点完把焦点还给游戏，展开后鼠标离开一段时间自动收起（无界面模式运行）。"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QPoint  # noqa: E402


class FakeEngine:
    def __init__(self):
        self.store = SimpleNamespace(heroes={}, resolve_hero=lambda key: {"提莫": "17"}.get(key))
        self.settings = SimpleNamespace(nas_url="")
        self.icons = None
        self.picked = []

    def hero_panel(self, _hid):
        return None

    def hero_brief(self, _hid):
        return {}

    def set_hero(self, hid):
        self.picked.append(hid)

    def rescan(self):
        pass


@pytest.fixture
def panel(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from lolhex.ui import overlay
    focused, clock, cursor = [], [1000.0], [QPoint(-500, -500)]
    monkeypatch.setattr(overlay.win, "give_focus", focused.append)
    monkeypatch.setattr(overlay.win, "add_companion", lambda _h: None)
    monkeypatch.setattr(overlay.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(overlay.QCursor, "pos", staticmethod(lambda: cursor[0]))
    p = overlay.ControlPanel(FakeEngine())
    p.target_hwnd = 4242
    p.show()
    yield SimpleNamespace(p=p, app=app, focused=focused, clock=clock, cursor=cursor, overlay=overlay)
    p.close()


def _wait(t, s):
    t.clock[0] += s


def test_click_gives_focus_back_to_game(panel):
    panel.p.btn.click()
    panel.app.processEvents()
    assert panel.p.expanded
    assert panel.focused == [4242]


def test_manual_open_hides_after_mouse_away(panel):
    p = panel.p
    p.btn.click()
    p.auto_hide("ingame")              # 开始计时
    _wait(panel, panel.overlay.AUTO_HIDE_SECONDS - 1)
    p.auto_hide("ingame")
    assert p.expanded
    _wait(panel, 1)
    p.auto_hide("ingame")
    assert not p.expanded


def test_hover_resets_timer(panel):
    p = panel.p
    p.btn.click()
    p.auto_hide("ingame")
    _wait(panel, panel.overlay.AUTO_HIDE_SECONDS - 1)
    panel.cursor[0] = p.frameGeometry().center()
    p.auto_hide("ingame")              # 鼠标在浮窗上：不收
    panel.cursor[0] = QPoint(-500, -500)
    p.auto_hide("ingame")              # 离开：重新开始计时（实际每 250ms 检查一次）
    _wait(panel, panel.overlay.AUTO_HIDE_SECONDS - 1)
    p.auto_hide("ingame")
    assert p.expanded                  # 离开后重新计时
    _wait(panel, 2)
    p.auto_hide("ingame")
    assert not p.expanded


def test_champ_select_auto_open_stays_until_click_or_game(panel):
    # 用户 10-22：选英雄时识别到英雄（或换英雄）自动展开；没换就不动，鼠标进出也不收；手动点一下收起；进游戏后自动收起
    p = panel.p
    p.update_from({"hero_id": "1", "phase": "client"})
    assert p.expanded and p._auto_opened
    for _ in range(3):
        _wait(panel, panel.overlay.AUTO_HIDE_SECONDS)
        p.auto_hide("client")
    panel.cursor[0] = p.frameGeometry().center()
    p.auto_hide("client")
    panel.cursor[0] = QPoint(-500, -500)
    for _ in range(3):
        _wait(panel, panel.overlay.AUTO_HIDE_SECONDS)
        p.auto_hide("client")
    assert p.expanded                  # 鼠标进出过也不收
    p.update_from({"hero_id": "1", "phase": "client"})
    assert p.expanded
    p.toggle()                         # 手动收起
    p.update_from({"hero_id": "1", "phase": "client"})
    assert not p.expanded              # 同一个英雄：不再自动展开
    p.update_from({"hero_id": "2", "phase": "client"})
    assert p.expanded                  # 换了英雄：再展开


def test_champ_select_auto_open_hides_in_game(panel):
    p = panel.p
    p.update_from({"hero_id": "1", "phase": "client"})
    p.auto_hide("ingame")
    _wait(panel, panel.overlay.AUTO_HIDE_SECONDS)
    p.auto_hide("ingame")
    assert not p.expanded


def test_search_pick_and_escape_give_focus_back(panel):
    p = panel.p
    p.btn.click()
    panel.app.processEvents()
    panel.focused.clear()
    p.search.setText("提莫")
    p._search_pick()
    panel.app.processEvents()
    assert p.engine.picked == ["17"] and panel.focused == [4242]
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    p.search.setText("abc")
    QtWidgets.QApplication.sendEvent(p.search, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    panel.app.processEvents()
    assert p.search.text() == "" and panel.focused == [4242, 4242]


@pytest.mark.parametrize("w,h", [(1024, 576), (1280, 720), (1600, 900), (1920, 1080)])
def test_lobby_badge_scales_with_client(panel, monkeypatch, w, h):
    # 1.1.1（A4）：大厅里图标放在头像右边「在线」那一行、铃铛左边，位置和大小按客户端窗口等比例
    from lolhex import cloud
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge({}))
    r = panel.overlay.win.Rect(100, 50, w, h)
    s = h / 720
    cx, cy, d = panel.overlay.client_badge(r)
    assert (cx, cy, d) == (round(100 + w - 60 * s), round(50 + 53 * s), round(26 * s))
    panel.p.place(r, in_client=True)   # 非 Windows：按逻辑像素摆
    b = panel.p.btn
    assert b.width() == max(20, d)
    assert abs(panel.p.x() + panel.p.width() - b.width() / 2 - cx) <= 1 and abs(panel.p.y() + b.height() / 2 - cy) <= 1
    # 进游戏后和以前一样：右上角、40 像素
    panel.p.place(r, in_client=False)
    assert b.width() == 40 and panel.p.y() == 50 + 90


def test_lobby_badge_cloud_override_and_bad_values(panel, monkeypatch):
    from lolhex import cloud
    r = panel.overlay.win.Rect(0, 0, 1280, 720)
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge({"ui": {"client_badge_right": 90, "client_badge_top": "70"}}))
    assert panel.overlay.client_badge(r) == (1280 - 90, 70, 26)
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge({"ui": {"client_badge_right": 9999, "client_badge_size": "x"}}))
    assert panel.overlay.client_badge(r) == (1280 - 60, 53, 26)   # 超出范围、认不出：用默认
    # 浮窗设在左上角：大厅里也按以前的位置
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge({}))
    panel.p.corner = "top-left"
    panel.p.place(r, in_client=True)
    assert panel.p.btn.width() == 40 and (panel.p.x(), panel.p.y()) == (12, 90)


def test_layers_register_drawn_areas_for_masking(panel, monkeypatch):
    # 1.1.1（A1）：浮窗不再对截图隐身；标签、浮窗画在哪都要登记，识别截图时涂掉
    win = panel.overlay.win
    monkeypatch.setattr(win, "_drawn", {})
    rect = win.Rect(200, 100, 1920, 1080)
    layer = panel.overlay.BadgeLayer()
    boxes = [[400 + 500 * i, 500, 760 + 500 * i, 540] for i in range(3)]
    snap = {"evaluation": {"slots": [{"index": i, "verdict": "ok", "score": 1.0} for i in range(3)]},
            "offer": {"boxes": boxes}, "settings": {}}
    layer.update_from(snap, rect)
    layer.grab()  # 触发 paintEvent
    labels = win.drawn_rects()
    labels = [r for r in labels if r.width < 400]  # 去掉 ControlPanel 那一块
    assert len(labels) == 3
    for r, b in zip(sorted(labels, key=lambda r: r.left), boxes):
        assert r.left <= (b[0] + b[2]) / 2 <= r.left + r.width     # 每张卡上面一块
        assert r.top + r.height <= b[1] + 8 and r.top >= rect.top    # 利用标题框上侧空白，仍不压到文字
    layer.hide()
    win.set_drawn("ControlPanel", [])
    monkeypatch.setattr(win.time, "monotonic", lambda: 1e12)
    assert win.drawn_rects() == []
    # 控制浮窗：显示着就登记整块
    panel.p.place(rect, in_client=False)
    panel.p._publish()
    cp = win.drawn_rects()
    assert len(cp) == 1 and cp[0].width >= panel.p.btn.width()


def test_champ_select_layer_registers_labels(panel, monkeypatch):
    win = panel.overlay.win
    monkeypatch.setattr(win, "_drawn", {})
    rect = win.Rect(0, 0, 1280, 720)
    layer = panel.overlay.ChampSelectLayer()
    cs = {"mine": "1", "team": [{"hero_id": "1", "box": [100, 100, 160, 160], "win": 0.5, "grade": "A"}],
          "bench": [{"hero_id": "2", "box": [400, 50, 450, 100], "win": 0.6}], "cards": []}
    layer.update_from({"champ_select": cs, "settings": {}}, rect)
    layer.grab()
    rs = [r for r in win.drawn_rects() if r.width < 600]
    assert len(rs) >= 2
    # 头像本身不涂（识别要看）
    for r in rs:
        assert not (r.left <= 130 <= r.left + r.width and r.top <= 130 <= r.top + r.height)
        assert not (r.left <= 425 <= r.left + r.width and r.top <= 75 <= r.top + r.height)


def test_toggle_keeps_icon_in_place(panel, monkeypatch):
    # 点图标展开/收起：浮窗在右上角时图标不能跳（以前收起时先跳到详情左边，下次摆放才回来）
    from lolhex import cloud
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge({}))
    p = panel.p
    p.place(panel.overlay.win.Rect(0, 0, 1920, 1080), in_client=True)
    icon = lambda: (p.x(), p.y(), p.width(), p.height())  # noqa: E731
    at = icon()
    p.toggle()
    # 图标窗口不变；详情是另一个窗口，贴在图标下面、右边对齐
    bw = p.body_win
    assert p.expanded and icon() == at and bw.isVisible() and bw.width() >= 360
    assert bw.x() + bw.width() == p.x() + p.width() and bw.y() >= p.y() + p.height()
    p.toggle()
    assert not p.expanded and icon() == at and not bw.isVisible()
    # 详情变高（换英雄、内容多了）：往下长，图标不动
    p.toggle()
    p.info.setText("<br>".join(["一行"] * 30))
    p._place_body()
    assert icon() == at and bw.x() + bw.width() == p.x() + p.width()



def test_publish_registers_icon_and_body_only(panel, monkeypatch):
    # 复核 10-22：展开后只登记图标和详情，图标左边那条透明区域不涂（不然盖住预选栏最后一格头像）
    from lolhex import cloud
    win = panel.overlay.win
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge({}))
    monkeypatch.setattr(win, "_drawn", {})
    p = panel.p
    p.place(win.Rect(0, 0, 1280, 720), in_client=True)
    p.toggle()
    p._publish()
    rs = win.drawn_rects()
    assert len(rs) == 2
    icon, body = sorted(rs, key=lambda r: r.top)
    assert icon.width < 60 and body.width >= 360 and body.top >= icon.top + icon.height
    # 图标左边、详情上面那块不登记
    probe = (body.left + 10, icon.top + icon.height // 2)
    assert not any(r.left <= probe[0] < r.left + r.width and r.top <= probe[1] < r.top + r.height for r in rs)


def test_auto_open_collapses_after_dodge(panel):
    # 选英雄自动展开后秒退回大厅：不在选英雄界面了，照常计时收起
    p = panel.p
    p.update_from({"hero_id": "1", "phase": "client"})
    p.auto_hide("client", False)
    _wait(panel, panel.overlay.AUTO_HIDE_SECONDS)
    p.auto_hide("client", False)
    assert not p.expanded
    p.update_from({"hero_id": None, "phase": "client"})
    p.update_from({"hero_id": "1", "phase": "client"})   # 下一把同一个英雄：再自动展开
    assert p.expanded
