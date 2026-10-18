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
