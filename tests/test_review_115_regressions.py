"""1.1.5 整体复核：数据更新后的面板与安装信息隔离。"""
import subprocess
import sys
import uuid

import pytest

from conftest import make_dataset
from lolhex import patch
from lolhex.config import Settings
from lolhex.engine import Engine


def test_incomplete_rollback_requires_installer_before_network(tmp_path, monkeypatch):
    (tmp_path / patch.ROLLBACK_INCOMPLETE).write_text("incomplete", encoding="utf-8")
    backup = tmp_path / patch.BACKUP / "AramHex.exe"
    backup.parent.mkdir()
    backup.write_bytes(b"only original copy")
    monkeypatch.setattr(patch, "fetch_manifest", lambda *a, **k: pytest.fail("must not download"))
    with pytest.raises(patch.NeedFullInstall, match="完整还原"):
        patch.prepare("1.1.5", root=tmp_path)
    assert backup.read_bytes() == b"only original copy"


def test_manual_statistics_activation_refreshes_same_hero_panel(store, tmp_path):
    engine = Engine(Settings(), home=tmp_path / "home", store=store)
    before = engine.hero_panel("17")
    make_dataset(store.downloaded, seq=store.dataset.seq + 1, heroes={
        "17": {"games": 400000, "wins": 240000, "win_rate": 0.6},
        "1": {"games": 300000, "wins": 147000, "win_rate": 0.49},
    })
    assert store.reload_dataset()
    after = engine.hero_panel("17")
    assert before["rank"]["win"] == 0.53
    assert after["rank"]["win"] == 0.6
    assert after["data_revision"] == store.dataset.revision
    assert after is not before
    assert engine.hero_panel("17") is after


@pytest.mark.parametrize("change", ["show_winrate", "show_sample_size", "show_pick_rate", "delta", "show_rate"])
def test_destiny_hint_repaints_when_its_display_or_delta_changes(monkeypatch, change):
    import copy
    from PySide6.QtWidgets import QApplication
    from lolhex.ui import overlay

    app = QApplication.instance() or QApplication([])
    layer = overlay.BadgeLayer()
    updates = []
    monkeypatch.setattr(layer, "update", lambda *args: updates.append(args))
    snap = {"settings": {"show_winrate": True},
            "offer": {"boxes": [[700, 430, 1000, 470]]},
            "evaluation": {"slots": [{"index": 0, "verdict": "best", "score": 2,
                "basis": "tier_only", "item_hint": {"source": "stats", "items": [
                    {"item": "4645", "name": "影焰", "games": 1000, "win": .55}]}}]}}
    try:
        rect = overlay.win.Rect(0, 0, 1920, 1080)
        layer.update_from(snap, rect)
        updates.clear()
        changed = copy.deepcopy(snap)
        hint = changed["evaluation"]["slots"][0]["item_hint"]
        if change == "show_winrate":
            changed["settings"]["show_winrate"] = False
        elif change == "delta":
            hint["items"][0]["delta"] = 2
        elif change == "show_rate":
            hint[change] = False
        else:
            hint[change] = True
        layer.update_from(changed, rect)
        assert len(updates) == 1 and not updates[0][0].isEmpty()
        updates.clear()
        layer.update_from(changed, rect)
        assert updates == []
    finally:
        layer.close()
        app.processEvents()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry and PowerShell")
@pytest.mark.parametrize("portable,location,expected", [
    (True, "same", "1.1.4"),
    (True, "other", "1.1.4"),
    (False, "other", "1.1.4"),
    (False, "missing", "1.1.4"),
    (False, "same", "1.1.5"),
])
def test_apply_updates_only_matching_installed_copy(tmp_path, monkeypatch, portable, location, expected):
    import winreg

    # 每个用例只使用独立的临时键，不触碰真实卸载信息。
    key_path = "Software\\AramHexCodexTest-" + uuid.uuid4().hex
    monkeypatch.setattr(patch, "UNINSTALL_KEY", key_path)
    root = tmp_path / "应用 目录"
    root.mkdir()
    (root / "AramHex.exe").write_bytes(b"old")
    (root / patch.STAGING).mkdir()
    (root / patch.STAGING / "AramHex.exe").write_bytes(b"new")
    if portable:
        (root / "portable.txt").write_bytes(b"")
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, "1.1.4")
            if location != "missing":
                install = str(root).upper() + "\\" if location == "same" else str(tmp_path / "other")
                winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, install)
        # launch_apply 必须等待已经退出的 PID，不能误结束测试进程。
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=10)
        patch.launch_apply(root, ["AramHex.exe"], [], "1.1.5", pid=child.pid, wait=True)
        assert (root / "AramHex.exe").read_bytes() == b"new"
        assert not (root / "update-failed.txt").exists()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            assert winreg.QueryValueEx(key, "DisplayVersion")[0] == expected
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
