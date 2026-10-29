"""「检查更新」按钮也马上查官方资料（1.1.3）。"""
import os
from types import SimpleNamespace

import pytest

from lolhex.data.store import DataStore


class FakeOfficial:
    def __init__(self, changed):
        self.changed, self.calls = changed, []

    def sync(self, force=False):
        self.calls.append(force)
        return self.changed

    def status(self):
        return {"official_revision": 2, "official_error": None}


def test_sync_official_forces_and_clears_hero_cache(tmp_path):
    st = DataStore(tmp_path)
    st.official = FakeOfficial(True)
    st._heroes["17"] = {"old": True}
    assert st.sync_official(force=True) is True
    assert st.official.calls == [True] and st._heroes == {}


def test_refresh_still_uses_interval(tmp_path):
    st = DataStore(tmp_path)
    st.official = FakeOfficial(False)
    st.refresh()
    assert st.official.calls == [False]


def test_sync_official_without_managed_official_does_nothing(tmp_path):
    assert DataStore(tmp_path).sync_official(force=True) is False


@pytest.mark.parametrize("changed, extra", [(True, "官方资料已更新（第 2 版）"), (False, None)])
def test_manual_check_message(changed, extra):
    pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from lolhex.ui.mainwindow import MainWindow

    official = FakeOfficial(changed)
    store = SimpleNamespace(sync_data=lambda **k: False, data_note=None, sync_error=None,
                            dataset=SimpleNamespace(patch="16.21", snapshot_at="2026-10-26T11:41:58+08:00"),
                            official=official)
    store.sync_official = lambda force=False: DataStore.sync_official(
        SimpleNamespace(official=official, _lock=__import__("threading").RLock(), _heroes={}, last_error=None), force)
    fake = SimpleNamespace(engine=SimpleNamespace(store=store))
    msg = MainWindow._sync_data_now(fake)
    assert msg.startswith("统计数据已是最新（16.21 版本，截至 10-26）。")
    assert official.calls == [True]
    assert (extra in msg) if extra else ("官方资料" not in msg)
