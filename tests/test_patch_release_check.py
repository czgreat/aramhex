"""发版前检查：上一版的用户能不能增量更新到这一版。"""
import json

import pytest

from lolhex import patch

BASE = {"AramHex.exe": "1", "_internal/base_library.zip": "1", "_internal/lolhex/a.pyd": "1", "_internal/cv2/cv2.pyd": "1",
        "_internal/cv2/config-3.py": "c", "_internal/msvcp140.dll": "1", "_internal/numpy-1.dist-info/RECORD": "1",
        "_internal/rapidocr/x.yaml": "1"}


def make(root, files):
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    return patch.build_manifest(root, "1.3.0")


def check(tmp_path, change=(), drop=()):
    old = make(tmp_path / "old", BASE)
    return patch.blockers(old, make(tmp_path / "new", {k: v for k, v in {**BASE, **dict(change)}.items() if k not in drop}))


def test_same_third_party_files_can_update(tmp_path):
    assert check(tmp_path, {"AramHex.exe": "2", "_internal/lolhex/_release.v2": "x", "_internal/msvcp140.dll": "2",
                            "_internal/numpy-1.dist-info/RECORD": "2", patch.CV2_PIN: "c"},
                 drop=("_internal/lolhex/a.pyd", "_internal/rapidocr/x.yaml")) == []


@pytest.mark.parametrize("change,drop,name", [
    ({"_internal/cv2/cv2.pyd": "2"}, (), "cv2.pyd"), ({"_internal/extra.pyd": "1"}, (), "extra.pyd"),
    ({"_internal/rapidocr/x.yaml": "2"}, (), "x.yaml"), ({}, ("_internal/msvcp140.dll",), "msvcp140.dll")])
def test_what_stops_an_incremental_update(tmp_path, change, drop, name):
    assert [x for x in check(tmp_path, change, drop) if name in x] == check(tmp_path, change, drop) != []


def test_file_name_case_does_not_matter(tmp_path):
    old = make(tmp_path / "old", BASE)
    new = make(tmp_path / "new", {k.replace("msvcp140", "MSVCP140"): v for k, v in BASE.items()})
    assert patch.blockers(old, new) == []


def test_release_command_stops(tmp_path, monkeypatch):
    old = make(tmp_path / "old", BASE)
    (tmp_path / "prev.json").write_text(json.dumps(old), encoding="utf-8")
    make(tmp_path / "new", {**BASE, "_internal/cv2/cv2.pyd": "2"})
    with pytest.raises(SystemExit, match="cv2.pyd"):
        patch.main([str(tmp_path / "new"), "1.3.0", str(tmp_path / "out"), str(tmp_path / "prev.json")])
    assert not (tmp_path / "out").exists()
    make(tmp_path / "new2", {**BASE, patch.CV2_PIN: "c"})
    assert patch.main([str(tmp_path / "new2"), "1.3.0", str(tmp_path / "out"), str(tmp_path / "prev.json")]) == 0
    assert patch.CV2_PIN in json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))["patchable"]
