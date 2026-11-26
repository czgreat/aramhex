from types import SimpleNamespace

from lolhex.vision import window


def test_client_rect_requires_screen_coordinate_conversion(monkeypatch):
    hwnd = 0x100000123

    def get_client_rect(handle, rect):
        assert handle == hwnd
        rect._obj.right, rect._obj.bottom = 800, 600
        return True

    def client_to_screen(handle, point):
        assert handle == hwnd
        point._obj.x, point._obj.y = -900, 125
        return True

    user32 = SimpleNamespace(IsIconic=lambda _: False, IsWindowVisible=lambda _: True,
                             GetClientRect=get_client_rect, ClientToScreen=client_to_screen)
    monkeypatch.setattr(window, "IS_WINDOWS", True)
    monkeypatch.setattr(window.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)

    assert window.client_rect(hwnd) == window.Rect(-900, 125, 800, 600)
    user32.ClientToScreen = lambda *_: False
    assert window.client_rect(hwnd) is None
