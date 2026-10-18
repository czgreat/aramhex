from PySide6.QtWidgets import QApplication

from lolhex.ui import mainwindow


class _Engine:
    stopped = False

    def stop(self):
        self.stopped = True


def test_quit_stops_engine_ends_loop_and_arms_hard_exit(monkeypatch):
    app = QApplication.instance() or QApplication([])
    timers, exits = [], []
    monkeypatch.setattr(mainwindow, "hard_exit_later", lambda s, reason="": timers.append((s, reason)))
    monkeypatch.setattr(QApplication, "exit", staticmethod(lambda code=0: exits.append(code)))
    eng = _Engine()
    try:
        mainwindow.quit_app(eng)
        assert eng.stopped and exits == [0]
        assert timers == [(mainwindow.QUIT_TIMEOUT_S, "退出超时，强制结束")]
        assert app.property("quitting") is True
    finally:
        app.setProperty("quitting", False)
