"""连续场景回归：检查画面换了以后不会继续使用上一份状态。"""

from lolhex.config import Settings
from lolhex.engine import Engine
from lolhex.vision.detector import Offer


def test_same_cards_after_window_move_publish_new_positions(store, tmp_path, monkeypatch):
    eng = Engine(Settings(), home=tmp_path, store=store)
    eng._set(hero_id="17", phase="ingame")
    evaluations = []
    original = eng._reevaluate
    monkeypatch.setattr(eng, "_reevaluate", lambda *args: (evaluations.append(True), original(*args))[1])
    first = Offer(["2128", "1029", "1373"], ["a", "b", "c"], [100]*3,
                  [(100, 430, 300, 470), (500, 430, 700, 470), (900, 430, 1100, 470)])
    eng._on_offer(first)
    old_evaluation = eng.state["evaluation"]
    moved = Offer(first.slots, first.texts, first.scores,
                  [(x+640, y+120, r+640, b+120) for x, y, r, b in first.boxes])
    eng._on_offer(moved)
    assert eng.state["offer"]["boxes"] == moved.boxes
    assert eng.state["evaluation"] is old_evaluation
    assert len(evaluations) == 1


def test_evaluation_finishing_after_game_end_cannot_restore_old_result(store, tmp_path, monkeypatch):
    import lolhex.engine as module
    eng = Engine(Settings(), home=tmp_path, store=store)
    eng._set(hero_id="17", offer={"slots": ["2128", "1029", "1373"]})
    original = module.recommend.evaluate
    def end_while_computing(*args, **kwargs):
        result = original(*args, **kwargs)
        eng._on_game_end()
        return result
    monkeypatch.setattr(module.recommend, "evaluate", end_while_computing)
    eng._reevaluate()
    assert eng.state["hero_id"] is None
    assert eng.state["offer"] is None
    assert eng.state["evaluation"] is None


def test_lobby_wait_keeps_hero_until_next_champ_select(store, tmp_path, monkeypatch):
    import lolhex.engine as module
    now = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(Engine, "_prefetch_hero", lambda *args: None)
    eng = Engine(Settings(), home=tmp_path, store=store)
    eng._apply_champ_select({"mine": "17", "team": [], "bench": []})
    eng._apply_champ_select(False)
    now[0] += 2
    eng._apply_champ_select(None)  # 短暂识别不到仍是同一局。
    assert eng.state["hero_id"] == "17"
    eng._apply_champ_select(False)
    now[0] += 10
    eng._apply_champ_select(False)  # 也可能是慢启动，不能仅凭大厅画面清身份。
    assert eng.state["hero_id"] == "17"
    eng._apply_champ_select(None)  # 下一次确认进入选人，认出新英雄前先清旧身份。
    assert eng.state["hero_id"] is None
    assert eng.state["offer"] is None and eng.state["evaluation"] is None


def test_newer_data_recalculation_wins_over_older_inflight_result(store, tmp_path, monkeypatch):
    import lolhex.engine as module
    eng = Engine(Settings(), home=tmp_path, store=store)
    eng._set(hero_id="17", offer={"slots": ["2128", "1029", "1373"]})
    original = module.recommend.evaluate
    active = [False]
    def replace_data_while_computing(*args, **kwargs):
        result = original(*args, **kwargs)
        if not active[0]:
            active[0] = True
            eng._reevaluate()
            result["test_generation"] = "old"
        else:
            result["test_generation"] = "new"
        return result
    monkeypatch.setattr(module.recommend, "evaluate", replace_data_while_computing)
    eng._reevaluate()
    assert eng.state["evaluation"]["test_generation"] == "new"


def test_game_end_then_new_game_has_no_previous_card_or_manual_hero(store, tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "_prefetch_hero", lambda *args: None)
    eng = Engine(Settings(), home=tmp_path, store=store)
    eng.set_hero("17")
    eng._set(phase="ingame")
    eng._on_offer(Offer(["2128", "1029", "1373"], [], [], [None]*3))
    eng._on_game_end()
    assert not eng._manual_hero
    assert eng.snapshot()["hero"] is None
    assert eng.state["offer"] is None and eng.state["evaluation"] is None
    eng._apply_champ_select({"mine": "1", "team": [], "bench": []})
    assert eng.state["hero_id"] == "1"
    assert eng.state["offer"] is None


def test_repeated_hero_and_cards_in_new_game_are_logged_again(store, tmp_path, caplog):
    import logging
    eng = Engine(Settings(), home=tmp_path, store=store)
    with caplog.at_level(logging.INFO, logger="lolhex.engine"):
        for _ in range(2):
            eng._set(hero_id="17", phase="ingame")
            eng._on_offer(Offer(["2128", "1029", "1373"], [], [], [None]*3))
            eng._on_game_end()
    assert len([r for r in caplog.records if "三选一（" in r.getMessage()]) == 2


def test_offer_scan_finishing_after_return_to_client_is_ignored(store, tmp_path):
    eng = Engine(Settings(), home=tmp_path, store=store)
    eng._set(phase="ingame", hero_id="17")
    eng._set(phase="client")
    eng._on_game_end()
    eng._on_offer(Offer(["2128", "1029", "1373"], [], [], [None]*3))
    assert eng.state["offer"] is None and eng.state["evaluation"] is None


def test_detector_pixel_cache_does_not_reuse_old_screen_coordinates():
    import numpy as np
    from lolhex.vision.detector import AugmentDetector
    from lolhex.vision.matcher import NameMatcher
    from lolhex.vision.window import Rect
    det = AugmentDetector(NameMatcher({"1": "虚幻武器"}))
    det.ensure_calibration(1920, 1080)
    class OCR:
        def recognize(self, image):
            return "虚幻武器", 1.0
    det._ocr = OCR()
    strip = np.zeros((80, 1500, 3), dtype=np.uint8)
    first = det.check_strip(strip, Rect(0, 0, 1920, 1080))
    moved = det.check_strip(strip, Rect(-640, 120, 1920, 1080))
    assert moved.boxes == [(x-640, y+120, r-640, b+120) for x, y, r, b in first.boxes]
