"""助手常驻托盘好几天：每次查分、查更新的线程对象跑完要删掉，选人缓存不能随窗口尺寸无限变多。"""
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QThread
from PySide6.QtWidgets import QApplication


def _settle(workers):
    for w in workers:
        w.wait(5000)
    for _ in range(3):
        QCoreApplication.processEvents()  # 送达跨线程的 finished
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_rank_and_update_workers_are_deleted_after_finishing(store, tmp_path, monkeypatch):
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui import mainwindow

    monkeypatch.setattr(mainwindow.RankWorker, "run", lambda self: None)
    monkeypatch.setattr(mainwindow.UpdateWorker, "run", lambda self: None)
    QApplication.instance() or QApplication([])
    engine = Engine(Settings(), home=tmp_path, store=store)
    window = mainwindow.MainWindow(engine, SimpleNamespace(set_visible=lambda *args: None))
    try:
        for _ in range(5):
            window._query_rank(silent=True)
            rank_worker = window._rank_worker
            window.check_update()
            _settle([rank_worker, window._worker])
        assert window._rank_worker is None and window._worker is None
        assert window.findChildren(QThread) == []
    finally:
        window.deleteLater()


def test_champ_select_memo_is_bounded_when_window_size_keeps_changing():
    from lolhex.vision.champselect import Memo

    memo = Memo()
    for h in range(20, 20 + Memo.LIMIT + 50):
        memo("team0", np.full((h, 120, 3), 90, np.uint8), lambda crop: "17")
    assert len(memo._d) <= Memo.LIMIT
    # 同一尺寸反复识别仍只占一格，正常对局不会触发清空
    same = Memo()
    for _ in range(Memo.LIMIT + 50):
        same("team0", np.full((40, 120, 3), 90, np.uint8), lambda crop: "17")
    assert len(same._d) == 1


def test_game_end_drops_champ_select_memo(store, tmp_path):
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.vision.champselect import Memo

    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._cs_memo = Memo()
    engine._on_game_end()
    assert engine._cs_memo is None


def test_vision_thread_releases_capture_outside_game(store, tmp_path, monkeypatch):
    import threading
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.vision import capture

    engine = Engine(Settings(), home=tmp_path, store=store)
    released = []

    def release():
        released.append(threading.current_thread().name)
        engine._stop.set()
    monkeypatch.setattr(capture, "release", release)
    engine.state["phase"] = "client"
    t = threading.Thread(target=engine._vision_loop, name="vision", daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive()
    assert released == ["vision"]
