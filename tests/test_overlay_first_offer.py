"""1.1.7：按钮放大、感应范围加大；对局开始到第一次海克斯选完按钮固定显示，选完半秒后连详情一起隐藏。"""
from PySide6.QtCore import QPoint

from lolhex.ui import overlay
from lolhex.vision.detector import Offer
from test_overlay_on_demand import scene  # noqa: F401  复用按需按钮的场景
from test_mainwindow_cloud import mw  # noqa: F401  真主窗口


def _offer(engine):
    engine._on_offer(Offer(["1029", "2128", "1373"], [""] * 3, [100.0] * 3,
                           [(100, 400, 300, 450), (500, 400, 700, 450), (900, 400, 1100, 450)], padded=True))


def test_wider_hover_zone(scene):
    snap = scene.engine.snapshot()
    edge = scene.rect.right - 12 - overlay.GAME_BUTTON - overlay.REVEAL_PAD + 2  # 按钮左边再往左 22 像素
    scene.cursor[0] = QPoint(edge, scene.rect.top + 110)
    assert scene.p.button_needed(snap, scene.rect, True)
    scene.cursor[0] = QPoint(edge - 10, scene.rect.top + 110)
    scene.clock[0] += 5
    assert not scene.p.button_needed(snap, scene.rect, True)


def _new_game(scene):
    """场景默认已过第一次选择；回到客户端再进对局，从头开始算。"""
    scene.engine._set(phase="client")
    scene.ov._tick()
    scene.engine._set(phase="ingame")
    scene.ov._tick()
    scene.clock[0] += overlay.AUTO_HIDE_SECONDS + 1  # 选英雄时自动展开的，进游戏后鼠标不在浮窗上按时收起
    scene.ov._tick()
    assert not scene.p.expanded
    return scene.p._ingame_since


def test_button_stays_until_first_offer_then_hides(scene):
    _new_game(scene)
    assert scene.p.isVisible() and not scene.p.expanded  # 鼠标不在附近也显示，只是按钮，不展开
    scene.clock[0] += 60
    scene.ov._tick()
    assert scene.p.isVisible()
    _offer(scene.engine)
    scene.ov._tick()
    assert scene.p.isVisible() and not scene.p.expanded  # 选卡时不展开，免得盖住卡
    scene.engine._on_offer(None)
    scene.ov._tick()
    assert scene.p.isVisible()  # 选完半秒内还在
    scene.clock[0] += overlay.FIRST_OFFER_HIDE_SECONDS
    scene.ov._tick()
    assert not scene.p.isVisible() and not scene.p.expanded
    scene.cursor[0] = QPoint(scene.rect.right - 30, scene.rect.top + 110)  # 鼠标移过去再出现
    scene.ov._tick()
    assert scene.p.isVisible()
    scene.cursor[0] = QPoint(-9999, -9999)
    scene.clock[0] += 5
    _offer(scene.engine)  # 第二次选择结束后不再固定显示
    scene.ov._tick()
    scene.engine._on_offer(None)
    scene.ov._tick()
    scene.clock[0] += 1
    scene.ov._tick()
    assert not scene.p.isVisible()
    _new_game(scene)  # 下一局重新固定显示
    assert scene.p.isVisible()


def test_open_details_close_with_the_button(scene):
    _new_game(scene)
    scene.p.toggle()
    scene.ov._tick()
    assert scene.p.expanded
    _offer(scene.engine)
    scene.ov._tick()
    scene.engine._on_offer(None)
    scene.ov._tick()
    assert scene.p.expanded
    scene.clock[0] += overlay.FIRST_OFFER_HIDE_SECONDS
    scene.ov._tick()
    assert not scene.p.expanded and not scene.p.isVisible() and not scene.p.body_win.isVisible()


def test_missing_hero_keeps_details_open(scene):
    scene.engine._set(phase="client", hero_id=None)
    scene.ov._tick()
    scene.engine._set(phase="ingame")
    scene.ov._tick()
    assert scene.p.expanded and scene.p.isVisible()  # 没认出英雄：展开提示手动选
    _offer(scene.engine)
    scene.ov._tick()
    scene.engine._on_offer(None)
    scene.clock[0] += 10
    scene.ov._tick()
    assert scene.p.expanded and scene.p.isVisible()  # 第一次选完也不收
    scene.engine.set_hero("17")
    scene.ov._tick()
    scene.clock[0] += overlay.AUTO_HIDE_SECONDS + 1
    scene.ov._tick()
    scene.clock[0] += overlay.AUTO_HIDE_SECONDS + 1
    scene.ov._tick()
    assert not scene.p.expanded and not scene.p.isVisible()  # 选好英雄后照常收起，按钮跟着隐藏


def test_pin_ends_if_no_offer_ever_seen(scene):
    """中途才开助手、等不到第一次选择：进对局一段时间后也不再固定显示。"""
    since = _new_game(scene)
    scene.clock[0] = since + overlay.PIN_MAX_SECONDS - 1
    scene.ov._tick()
    assert scene.p.isVisible()
    scene.clock[0] += 1
    scene.ov._tick()
    assert not scene.p.isVisible()


def test_hotkey_field_explains_single_keys_right_away(mw):
    from PySide6.QtGui import QKeySequence
    window = mw[0]
    window.set_hotkey.setKeySequence(QKeySequence("F8"))
    assert "Ctrl 或 Alt" in window.hotkey_status.text()
