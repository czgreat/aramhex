"""选完英雄卡：卡片没了先撤掉卡下胜率，不等后面认预选栏/我方（画面刚变要 1–2 秒）。"""

import threading
from types import SimpleNamespace

import numpy as np

from lolhex.engine import Engine
from lolhex.vision import champselect


def make(monkeypatch, cards_seq):
    eng = Engine.__new__(Engine)
    eng._lock = threading.RLock()
    eng.settings = SimpleNamespace(game_lang="zh_CN")
    eng._cs_memo = champselect.Memo()
    eng._icon_bank = object()
    eng.state = {"champ_select": None}
    eng.hero_brief = lambda hid: {"win": 0.5}
    seen_during_read = []
    monkeypatch.setattr("lolhex.vision.ocr.OCR.get", staticmethod(lambda: SimpleNamespace(recognize=None, detect=None)))
    monkeypatch.setattr(champselect, "is_champ_select", lambda *a, **k: True)
    monkeypatch.setattr(champselect, "read_cards", lambda *a, **k: cards_seq.pop(0))

    def slow_read(*a, **k):  # 认预选栏/我方：此时卡片胜率应该已经撤掉
        seen_during_read.append(dict(eng.state["champ_select"] or {}))
        return {"bench": [], "team": [{"slot": 0, "hero_id": "17"}], "mine": "17"}
    monkeypatch.setattr(champselect, "read", slow_read)
    return eng, seen_during_read


def test_cards_removed_before_slow_bench_read(monkeypatch):
    card = {"slot": 0, "hero_id": "17", "score": .9, "box": (0, 0, 1, 1)}
    eng, seen = make(monkeypatch, [[card, {**card, "slot": 1, "hero_id": "18"}], []])
    img = np.zeros((10, 10, 3), np.uint8)
    eng.state["champ_select"] = eng.analyze_champ_select(img)
    assert len(eng.state["champ_select"]["cards"]) == 2
    eng.analyze_champ_select(img)  # 这一轮卡片没了
    assert seen[-1] and "cards" not in seen[-1]      # 慢的那步开始前已撤掉
    assert eng.state["champ_select"]                 # 选英雄状态还在（主浮窗不收起）


def test_nothing_changes_while_cards_still_there(monkeypatch):
    card = {"slot": 0, "hero_id": "17", "score": .9, "box": (0, 0, 1, 1)}
    eng, seen = make(monkeypatch, [[card, {**card, "slot": 1}], [card, {**card, "slot": 1}]])
    img = np.zeros((10, 10, 3), np.uint8)
    eng.state["champ_select"] = eng.analyze_champ_select(img)
    before = eng.state["champ_select"]
    eng.analyze_champ_select(img)
    assert eng.state["champ_select"] is before
