import sys
from types import SimpleNamespace

from lolhex.ui import autostart


def test_fresh_account_without_run_key(monkeypatch):
    calls = []
    class Key:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def missing(*args):
        raise FileNotFoundError("Run key absent")
    reg = SimpleNamespace(HKEY_CURRENT_USER=1, KEY_SET_VALUE=2, REG_SZ=3,
                          OpenKey=missing, CreateKeyEx=lambda *args: Key(),
                          SetValueEx=lambda *args: calls.append(args))
    monkeypatch.setitem(sys.modules, "winreg", reg)
    monkeypatch.setattr(sys, "platform", "win32")
    autostart.set_enabled(False)
    assert calls == []  # 关闭时不创建空项、更不会默认启用
    autostart.set_enabled(True)
    assert calls[0][1] == "AramHex" and "--minimized" in calls[0][-1]
