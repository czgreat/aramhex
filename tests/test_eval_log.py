"""三选一评估结果写日志。"""


def test_engine_logs_evaluation_once(store, tmp_path, caplog):
    # 每个三选一记一行评估结果（同一个只记一次），方便事后查问题
    import logging
    from lolhex.config import Settings
    from lolhex.engine import Engine
    eng = Engine(Settings(), home=tmp_path, store=store)
    eng.state.update(hero_id="17", offer={"slots": ["2128", "1029", "1373"]})
    with caplog.at_level(logging.INFO, logger="lolhex.engine"):
        eng._reevaluate()
        eng._reevaluate()
    lines = [r.getMessage() for r in caplog.records if "三选一" in r.getMessage()]
    assert len(lines) == 1 and "提莫" in lines[0] and "虚幻武器 best" in lines[0] and "首选" in lines[0]


def test_new_champ_select_clears_previous_hero(store, tmp_path, monkeypatch):
    # 选英雄阶段有人秒退：没进游戏，下一把选英雄时要先清掉上一把的英雄（10-21 玩家反馈）
    from lolhex.config import Settings
    from lolhex.engine import Engine
    import lolhex.engine as E
    now = [1000.0]
    monkeypatch.setattr(E.time, "monotonic", lambda: now[0])
    eng = Engine(Settings(), home=tmp_path, store=store)
    eng._apply_champ_select({"bench": [], "team": [], "mine": "17"})
    assert eng.state["hero_id"] == "17"
    now[0] += 3
    eng._apply_champ_select(False)          # 偶尔一次没认出界面：不算离开
    now[0] += 2
    eng._apply_champ_select(None)           # 还在选英雄界面（什么都没认出）
    assert eng.state["hero_id"] == "17"
    now[0] += 1                             # 秒退，回大厅（扫描看到不在选英雄界面）
    eng._apply_champ_select(False)
    now[0] += 60                            # 排队一分钟后进入新的选英雄
    eng._apply_champ_select(None)
    assert eng.state["hero_id"] is None and not eng._manual_hero
    eng._apply_champ_select({"bench": [], "team": [], "mine": "1"})
    assert eng.state["hero_id"] == "1"
    eng.set_hero("17")                      # 这一把手动改了英雄：同一把里不清
    now[0] += 1
    eng._apply_champ_select({"bench": [], "team": [], "mine": "1"})
    assert eng.state["hero_id"] == "17"


def test_alt_tab_in_champ_select_keeps_manual_hero(store, tmp_path, monkeypatch):
    # 复核 10-22：选英雄时切到浏览器或助手主窗口（客户端不在前台，不扫描）超过 8 秒，回来不能当成新的一把
    from lolhex.config import Settings
    from lolhex.engine import Engine
    import lolhex.engine as E
    now = [1000.0]
    monkeypatch.setattr(E.time, "monotonic", lambda: now[0])
    eng = Engine(Settings(), home=tmp_path, store=store)
    eng._apply_champ_select({"bench": [], "team": [], "mine": "1"})
    eng.set_hero("17")
    now[0] += 40                            # 切出去 40 秒，期间没扫描
    eng._apply_champ_select({"bench": [], "team": [], "mine": "1"})
    assert eng.state["hero_id"] == "17" and eng._manual_hero
    now[0] += 100                           # 100 秒都没看到选英雄界面（早就不是这一把了）
    eng._apply_champ_select({"bench": [], "team": [], "mine": "1"})
    assert eng.state["hero_id"] == "1" and not eng._manual_hero
