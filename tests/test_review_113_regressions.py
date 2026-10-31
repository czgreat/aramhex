"""Independent 1.1.3 review findings: official UI, bad calibration and update paths."""
import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from lolhex import patch
from lolhex.vision.detector import AugmentDetector, Calibration
from lolhex.vision.matcher import NameMatcher


@pytest.mark.parametrize("change", ["revision", "origin"])
def test_mainwindow_refreshes_same_day_official_snapshot(tmp_path, monkeypatch, change):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui.mainwindow import MainWindow

    app = QApplication.instance() or QApplication([])
    settings = Settings(seen_welcome=True)
    engine = Engine(settings, home=tmp_path)
    engine.state.update(hero_id="17", phase="client")
    window = MainWindow(engine, SimpleNamespace(set_visible=lambda *a: None))
    renders = []

    def render(panel, show_win):
        text = json.dumps(panel["builds"]["core"], ensure_ascii=False)
        renders.append(text)
        return text

    monkeypatch.setattr(window, "_hero_html", render)
    for timer in window.findChildren(QTimer):
        timer.stop()
    try:
        window.show()
        window.refresh()
        old_text = window.tab_hero.text()
        old_panel = engine.hero_panel("17")
        old = engine.store.official.snapshot
        heroes = deepcopy(old.heroes)
        for row in heroes["17"]["builds"]["core"]:
            row["items"] = ["1001"] * 3
        new = replace(old, heroes=heroes,
                      manifest={**old.manifest, "revision": old.revision + (change == "revision")},
                      origin="downloaded" if change == "origin" else old.origin)
        engine.store.official.snapshot = new
        window.refresh()
        panel = engine.hero_panel("17")
        assert panel["date"] == old_panel["date"]
        assert panel["data_revision"] == old_panel["data_revision"]
        assert [item["id"] for item in panel["builds"]["core"][0]["items"]] == ["1001"] * 3
        assert window.tab_hero.text() != old_text
        count = len(renders)
        window.refresh()
        assert len(renders) == count  # Identical snapshots retain the render cache.
    finally:
        window.hide()
        window.deleteLater()
        engine.stop()
        app.sendPostedEvents()


@pytest.mark.parametrize("field,value", [
    ("size", ["1920", 1080]), ("size", [1920, None]), ("size", [True, 1080]),
    ("size", [10 ** 400, 1080]), ("size", None),
    ("boxes", [["0.2", .3, .4, .4], [.45, .3, .55, .4], [.65, .3, .75, .4]]),
    ("boxes", [[None, .3, .4, .4], [.45, .3, .55, .4], [.65, .3, .75, .4]]),
    ("boxes", [None, None, None]), ("boxes", None),
])
def test_invalid_calibration_types_fall_back_without_destroying_evidence(tmp_path, caplog, field, value):
    good = Calibration.default(1920, 1080)
    data = {"boxes": good.boxes, "size": good.size, field: value}
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    original = path.read_bytes()
    assert not Calibration(data["boxes"], data["size"]).valid()
    detector = AugmentDetector(NameMatcher({}), path, ocr=object())
    assert detector.calibration is None
    assert path.read_bytes() == original
    assert "校准" in caplog.text
    detector.ensure_calibration(1920, 1080)
    assert detector.calibration.valid()


def manifest_with(rel):
    body = b"isolated update fixture"
    return {"schema_version": 1, "version": "1.1.3", "patchable": [rel],
            "files": {rel: {"size": len(body), "sha256": hashlib.sha256(body).hexdigest()}}}


UNSAFE_PATHS = [
    r"..\..\outside.txt", r"_internal\..\..\outside.txt", r"C:\outside.txt", "C:outside.txt",
    r"\\server\share\outside.txt", "/outside.txt", "../outside.txt", "a/../outside.txt",
    "a//outside.txt", "a/./outside.txt", "a/", "", ".. /outside.txt", "a./outside.txt",
    "a:stream", "a?query", "a#fragment", "a/%2e%2e/outside.txt", "a/\x00outside.txt",
    "_UPDATE_NEW/outside.txt", "_update_old/outside.txt",
]


@pytest.mark.parametrize("rel", UNSAFE_PATHS)
def test_manifest_rejects_unsafe_windows_and_url_paths(rel):
    with pytest.raises(patch.NeedFullInstall):
        patch.check_manifest(manifest_with(rel), "1.1.3")


@pytest.mark.parametrize("operation", ["plan", "download", "apply_changed", "apply_stale"])
def test_patch_entry_points_reject_unchecked_parent_paths(tmp_path, monkeypatch, operation):
    rel = r"..\..\outside.txt"
    manifest = manifest_with(rel)
    app = tmp_path / "app"
    app.mkdir()
    calls = []
    monkeypatch.setattr("lolhex.data.http.fetch", lambda *a, **kw: calls.append(a) or b"isolated update fixture")
    with pytest.raises(patch.NeedFullInstall):
        if operation == "plan":
            patch.plan(manifest, app)
        elif operation == "download":
            patch.download(manifest, [rel], app, sources=["https://example.invalid"])
        else:
            patch.apply_script(app, [rel] if operation == "apply_changed" else [],
                               [rel] if operation == "apply_stale" else [], 1234, "1.1.3", None)
    assert calls == []
    assert not (tmp_path / "outside.txt").exists()


def test_download_rejects_staging_junction_escape(tmp_path, monkeypatch):
    import subprocess
    import sys

    app, outside = tmp_path / "app", tmp_path / "outside"
    app.mkdir()
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("untouched", encoding="utf-8")
    staging = app / patch.STAGING
    if sys.platform == "win32":
        subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(staging), str(outside)],
                       check=True, capture_output=True)
    else:
        staging.symlink_to(outside, target_is_directory=True)
    calls = []
    monkeypatch.setattr("lolhex.data.http.fetch", lambda *a, **kw: calls.append(a) or b"isolated update fixture")
    try:
        with pytest.raises(patch.NeedFullInstall):
            patch.download(manifest_with("AramHex.exe"), ["AramHex.exe"], app,
                           sources=["https://example.invalid"])
        assert marker.read_text(encoding="utf-8") == "untouched"
        assert not (outside / "AramHex.exe").exists()
        assert calls == []
    finally:
        if sys.platform == "win32":
            staging.rmdir()  # Remove only this test's junction, preserving its target.
        else:
            staging.unlink()
