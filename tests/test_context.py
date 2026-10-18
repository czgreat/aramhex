"""看对面阵容的海克斯（掷骰狂人）：提示文字、三选一里的提示、2999 读对面英雄。"""

from lolhex import context, recommend
from lolhex.game.liveclient import LiveClient

HEROES = {"90": {"name": "玛尔扎哈"}, "143": {"name": "婕拉"}, "1": {"name": "安妮"}, "57": {"name": "茂凯"}}


def test_enemy_note_lists_strongest_first():
    note = context.enemy_note("2095", ["1", "143", "90", "57"], HEROES)
    assert note == "对面玛尔扎哈 +5.1、婕拉 +4.5"  # 只列前两个；安妮不比平时强，不提
    assert context.enemy_note("2095", ["1"], HEROES) is None
    assert context.enemy_note("2095", [], HEROES) is None
    assert context.enemy_note("1029", ["90"], HEROES) is None  # 不看阵容的海克斯


def test_real_table_is_loaded():
    t = context.load()["augments"]["2095"]
    assert t["name"] == "掷骰狂人" and 1 < t["base"] < 3 and t["enemies"]["90"] > t["base"]


def test_evaluate_adds_note_without_changing_score(store):
    table = {"augments": {"1029": {"base": 1.0, "enemies": {"90": 4.0}}}}
    plain = recommend.evaluate(store, "17", ["2128", "1029", "1373"])
    orig = context._TABLE
    context._TABLE = table
    try:
        ev = recommend.evaluate(store, "17", ["2128", "1029", "1373"], enemies=["90"])
    finally:
        context._TABLE = orig
    assert ev["slots"][1]["context"].startswith("对面") and "+4.0" in ev["slots"][1]["context"]
    assert "context" not in ev["slots"][0]
    assert [s["score"] for s in ev["slots"]] == [s["score"] for s in plain["slots"]]


def test_liveclient_snapshot_splits_teams(monkeypatch):
    lc = LiveClient()
    data = {
        "/activeplayer": {"riotId": "我#1", "level": 7},
        "/playerlist": [
            {"riotId": "我#1", "team": "CHAOS", "rawChampionName": "game_character_displayname_Rumble", "championName": "机械公敌"},
            {"riotId": "友#2", "team": "CHAOS", "rawChampionName": "game_character_displayname_Taliyah", "championName": "岩雀"},
            {"riotId": "敌#3", "team": "ORDER", "rawChampionName": "game_character_displayname_Malzahar", "championName": "虚空先知"},
            {"riotId": "敌#4", "team": "ORDER", "rawChampionName": "game_character_displayname_Zyra", "championName": "荆棘之兴"}],
        "/gamestats": {"gameMode": "KIWI", "gameTime": 120.0},
    }
    monkeypatch.setattr(lc, "_get", lambda path: data.get(path))
    s = lc.snapshot()
    assert s["champion_raw"].endswith("Rumble") and s["level"] == 7
    assert s["enemies"] == ["game_character_displayname_Malzahar", "game_character_displayname_Zyra"]


def test_engine_logs_evaluation_once(store, tmp_path, caplog):
    # 测试版要靠日志查问题：每个三选一记一行评估结果（同一个只记一次）
    import logging
    from lolhex.config import Settings
    from lolhex.engine import Engine
    eng = Engine(Settings(), home=tmp_path, store=store)
    eng.state.update(hero_id="17", offer={"slots": ["2128", "1029", "1373"]}, enemies=["90"])
    with caplog.at_level(logging.INFO, logger="lolhex.engine"):
        eng._reevaluate()
        eng._reevaluate()
    lines = [r.getMessage() for r in caplog.records if "三选一" in r.getMessage()]
    assert len(lines) == 1 and "提莫" in lines[0] and "虚幻武器 best" in lines[0] and "首选" in lines[0]
