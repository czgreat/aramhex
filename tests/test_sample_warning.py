"""样本可信度提示不能因同屏三张都不足 1000 局而消失；不改变评分和选择规则。"""
import os
from types import SimpleNamespace

import pytest

from lolhex import recommend

IDS = ["2128", "1029", "1373"]


def evaluate(store, games, wins=(.90, .52, .51), slots=None):
    rows = store.dataset.table(None)[1]["17"]
    for aid, n, win in zip(IDS, games, wins):
        rows[aid] = {"games": n, "wins": round(n * win), "win_rate": win}
    return recommend.evaluate(store, "17", IDS if slots is None else slots)


def test_three_small_samples_warn_without_changing_scores_or_order(store):
    ev = evaluate(store, [97, 804, 729])
    assert ev["best_index"] == 0
    assert "三张样本都偏少" in ev["advice"] and "仅供参考" in ev["advice"]
    assert "首选" not in ev["advice"] and "刷新" not in ev["advice"]
    assert [s["sample"] for s in ev["slots"]] == ["low", "medium", "medium"]
    for row, games, win in zip(ev["slots"], [97, 804, 729], [.90, .52, .51]):
        assert row["sample_warning"] and not row.get("thin")
        assert any("样本偏少" in reason for reason in row["reasons"])
        assert (row["games"], row["win"]) == (games, win)
        assert row["score"] == round(recommend.smoothed_delta(win, games, ev["base_win"], 300), 2)


@pytest.mark.parametrize("games,thin", [([99, 1000, 999], "too_few"), ([100, 1000, 999], "few")])
def test_reliable_option_keeps_existing_thin_filter(store, games, thin):
    ev = evaluate(store, games)
    assert ev["slots"][0]["thin"] == thin
    assert ev["slots"][2]["thin"] == "few"
    assert ev["slots"][0]["score"] > ev["slots"][1]["score"]
    assert ev["best_index"] == 1
    assert ev["advice"].startswith("首选") and "三张样本" not in ev["advice"]
    assert ev["slots"][0]["sample_warning"] and ev["slots"][2]["sample_warning"]
    assert not ev["slots"][1].get("sample_warning")
    # 「样本太少」保留原有强提醒，不重复追加第二句「只有 N 局」。
    reasons = ev["slots"][0]["reasons"]
    assert sum(reason.startswith(f"只有 {games[0]} 局") for reason in reasons) == 1
    if thin == "too_few":
        assert any("少一个数量级" in reason for reason in reasons)


def test_three_reliable_samples_keep_normal_recommendation(store):
    ev = evaluate(store, [1000, 2000, 3000])
    assert ev["best_index"] == 0 and ev["advice"].startswith("首选")
    assert all(not r.get("sample_warning") and not r.get("thin") for r in ev["slots"])


@pytest.mark.parametrize("games,level,warn", [(249, "low", True), (250, "medium", True),
                                           (999, "medium", True), (1000, "high", False)])
def test_sample_warning_boundary(store, games, level, warn):
    row = evaluate(store, [games, 1500, 1800])["slots"][0]
    assert row["sample"] == level
    assert bool(row.get("sample_warning")) is warn


def test_incomplete_offer_does_not_claim_three_small_samples(store):
    ev = evaluate(store, [97, 804, 729], slots=[IDS[0], IDS[1], None])
    assert "三张样本" not in ev["advice"]
    assert "仅供参考" in ev["advice"] and "首选" not in ev["advice"]
    assert ev["slots"][0]["sample_warning"] and ev["slots"][1]["sample_warning"]
    assert ev["slots"][2]["verdict"] == "unknown"


def test_band_warning_uses_overall_games_without_changing_raw_band_stats(store):
    store.set_band("high")
    ev = recommend.evaluate(store, "17", ["1029", None, None])
    row = ev["slots"][0]
    assert row["games"] == 500 and row["games_all"] == 40000
    assert row["win"] == pytest.approx(.56)
    assert not row.get("sample_warning")
    store.dataset.table(None)[1]["17"]["1029"]["games"] = 999
    ev = recommend.evaluate(store, "17", ["1029", None, None])
    row = ev["slots"][0]
    assert row["games"] == 500 and row["games_all"] == 999
    assert row["win"] == pytest.approx(.56) and row["sample_warning"]
    assert any("999 局" in reason and "样本偏少" in reason for reason in row["reasons"])


def test_pbe_option_is_not_mislabeled_as_three_live_small_samples(store):
    evaluate(store, [97, 804, 729])
    store.dataset.meta["provisional_augments"] = {"3086": {"games": 5000, "win_rate": .56}}
    store.augments["3086"] = {"id": "3086", "name": "测试服样例", "rarity": "gold", "new": True}
    ev = recommend.evaluate(store, "17", [IDS[0], IDS[1], "3086"])
    assert "三张样本" not in ev["advice"]
    assert ev["best_index"] == 0 and "仅供参考" in ev["advice"]
    assert ev["slots"][2]["basis"] == "pbe" and not ev["slots"][2].get("sample_warning")


@pytest.fixture
def app(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def test_main_cards_display_warning_and_qualified_best(store, app):
    from PySide6.QtWidgets import QLabel
    from lolhex.ui.mainwindow import MainWindow
    target = SimpleNamespace(offer_advice=QLabel(), offer_cards=[QLabel() for _ in range(3)])
    MainWindow._render_offer(target, evaluate(store, [97, 804, 729]), True)
    assert "仅供参考" in target.offer_advice.text()
    assert all("样本偏少" in label.text() for label in target.offer_cards)
    assert "相对较好，仅供参考" in target.offer_cards[0].text()
    assert "首选" not in target.offer_cards[0].text()


def test_game_badges_display_warning_and_qualified_best(store, app, monkeypatch):
    from PySide6.QtGui import QImage, QPainter
    from lolhex.ui import overlay
    drawn = []

    class Painter(QPainter):
        def drawText(self, *args):
            drawn.append(args[-1])
            return super().drawText(*args)

    monkeypatch.setattr(overlay, "QPainter", Painter)
    layer = overlay.BadgeLayer()
    layer.resize(1920, 1080)
    layer.target_h = 1080
    layer.snapshot = {"evaluation": evaluate(store, [97, 804, 729]), "offer": {"boxes": [
        [440, 420, 750, 470], [810, 420, 1120, 470], [1180, 420, 1490, 470]]}}
    image = QImage(1920, 1080, QImage.Format_ARGB32)
    image.fill(0)
    try:
        layer.render(image)
        assert "相对较好 · 仅供参考" in drawn
        assert sum("样本偏少" in text for text in drawn) == 3
        assert "首选" not in drawn
    finally:
        layer.hide()
        layer.deleteLater()
