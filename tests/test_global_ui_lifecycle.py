"""UI behavior across settings, queued debug captures and background work."""
import threading
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication


def test_winrate_toggle_rebuilds_cached_hero_details(store, tmp_path):
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui.mainwindow import MainWindow

    QApplication.instance() or QApplication([])
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(hero_id='17')
    window = MainWindow(engine, SimpleNamespace(set_visible=lambda *args: None))
    try:
        window.show()
        window.refresh()
        before = window.tab_hero.text()
        engine.settings.show_winrate = False
        window.refresh()
        assert window.tab_hero.text() == window._hero_html(engine.hero_panel('17'), False)
        assert window.tab_hero.text() != before
        engine.settings.show_winrate = True
        window.refresh()
        assert window.tab_hero.text() == before
    finally:
        window.hide()
        window.deleteLater()


def test_autoshot_does_not_mark_busy_skipped_offer_as_saved(monkeypatch):
    from lolhex.ui import autoshot

    scheduled = []
    monkeypatch.setattr(autoshot.QTimer, 'singleShot', lambda ms, fn: scheduled.append(fn))
    shot = autoshot.AutoShot(SimpleNamespace(settings=SimpleNamespace(debug_mode=True)))
    snap = {'evaluation': {'x': 1}, 'offer': {'slots': ['1', '2', '3']}}
    shot._busy = True
    shot.tick(snap, 'rect', None)
    assert not scheduled
    shot._busy = False
    shot.tick(snap, 'rect', None)
    assert len(scheduled) == 1, 'a skipped offer was marked saved and never retried'


def test_autoshot_encoding_does_not_hold_up_qt_caller(monkeypatch):
    from lolhex.ui import autoshot

    returned, release, finished = threading.Event(), threading.Event(), threading.Event()
    caller = threading.get_ident()
    workers = []
    observed = []
    shot = autoshot.AutoShot(SimpleNamespace(settings=SimpleNamespace(debug_mode=True)))
    shot._busy = True

    def encode(*args):
        try:
            workers.append(threading.get_ident())
            assert release.wait(3), 'test did not release the simulated PNG encoder'
        finally:
            shot._busy = False
            finished.set()

    def unblock():
        observed.append(returned.wait(.75))
        release.set()

    monkeypatch.setattr(shot, '_grab', encode)
    helper = threading.Thread(target=unblock)
    helper.start()
    try:
        shot._open('rect', '三选一')
        returned.set()
        assert finished.wait(3)
    finally:
        release.set()
        helper.join(3)
    assert workers and workers[0] != caller, 'PNG capture/encoding ran on the Qt caller thread'
    assert observed == [True], 'Qt caller could not return while PNG encoding was waiting'


def test_busy_champ_select_capture_is_retried_after_worker_finishes(monkeypatch):
    from lolhex.ui import autoshot

    scheduled = []
    clock = [100.0]
    monkeypatch.setattr(autoshot.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(autoshot.QTimer, 'singleShot', lambda ms, fn: scheduled.append(fn))
    shot = autoshot.AutoShot(SimpleNamespace(settings=SimpleNamespace(debug_mode=True)))
    snap = {'champ_select': {'team': [{'grade': 'A'}]}}
    shot.tick(snap, None, 'rect')
    shot._busy = True
    clock[0] += 5
    shot.tick(snap, None, 'rect')
    assert not scheduled
    shot._busy = False
    shot.tick(snap, None, 'rect')
    assert len(scheduled) == 1
