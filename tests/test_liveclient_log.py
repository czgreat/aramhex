"""2999 读对面阵容（只记日志），以及三选一评估结果写日志。"""

from lolhex.game.liveclient import LiveClient


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
