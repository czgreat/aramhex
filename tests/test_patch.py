"""增量更新：清单、比较、下载校验、退出后替换文件（失败全部还原）。"""

import functools
import json
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from lolhex import patch


def _tree(root: Path, files: dict):
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    return root


OLD = {"AramHex.exe": b"exe-old", "_internal/base_library.zip": b"lib", "_internal/lolhex/data/a.json": b"a-old",
       "_internal/lolhex/data/gone.json": b"x", "_internal/cv2/cv2.pyd": b"opencv"}
NEW = {"AramHex.exe": b"exe-new", "_internal/base_library.zip": b"lib", "_internal/lolhex/data/a.json": b"a-new",
       "_internal/lolhex/data/b.json": b"b", "_internal/cv2/cv2.pyd": b"opencv"}


def test_plan_downloads_only_changed_files(tmp_path):
    m = patch.build_manifest(_tree(tmp_path / "new", NEW), "1.0.3")
    assert set(m["patchable"]) == {"AramHex.exe", "_internal/base_library.zip", "_internal/lolhex/data/a.json",
                                   "_internal/lolhex/data/b.json"}
    changed, stale = patch.plan(m, _tree(tmp_path / "old", OLD))
    assert sorted(changed) == ["AramHex.exe", "_internal/lolhex/data/a.json", "_internal/lolhex/data/b.json"]
    assert stale == ["_internal/lolhex/data/gone.json"]


def test_third_party_change_needs_full_installer(tmp_path):
    m = patch.build_manifest(_tree(tmp_path / "new", {**NEW, "_internal/cv2/cv2.pyd": b"opencv-2"}), "1.0.3")
    with pytest.raises(patch.NeedFullInstall, match="cv2"):
        patch.plan(m, _tree(tmp_path / "old", OLD))


def test_vc_runtime_change_is_patchable(tmp_path):
    # 构建机系统里的 VC++ 运行库更新了：跟着增量更新换，不要求重下完整安装包
    old = {**OLD, "_internal/msvcp140.dll": b"14.51", "_internal/PySide6/MSVCP140.dll": b"qt"}
    new = {**NEW, "_internal/msvcp140.dll": b"14.52", "_internal/PySide6/MSVCP140.dll": b"qt"}
    m = patch.build_manifest(_tree(tmp_path / "new", new), "1.1.0")
    assert "_internal/msvcp140.dll" in m["patchable"] and "_internal/PySide6/MSVCP140.dll" not in m["patchable"]
    changed, _ = patch.plan(m, _tree(tmp_path / "old", old))
    assert "_internal/msvcp140.dll" in changed


@pytest.mark.parametrize("bad", [{"version": "1.0.2"}, {"files": {"../evil": {"sha256": "0", "size": 1}}},
                                 {"patchable": ["missing"]}])
def test_manifest_checks(tmp_path, bad):
    m = {**patch.build_manifest(_tree(tmp_path / "new", NEW), "1.0.3"), **bad}
    with pytest.raises(patch.NeedFullInstall):
        patch.check_manifest(m, "1.0.3")


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _publish(srv: Path, sub: str, files: dict, version: str):
    """模拟 updates 分支：manifest.json + files/<相对路径>。"""
    built = _tree(srv / f"build-{sub}", files)
    patch.main([str(built), version, str(srv / sub)])


def test_prepare_uses_next_source_when_cache_is_stale(tmp_path, server):
    srv, base = server
    _publish(srv, "stale", OLD, "1.0.2")   # jsDelivr 缓存还是旧版本的清单
    _publish(srv, "fresh", NEW, "1.0.3")
    root = _tree(tmp_path / "app", OLD)
    changed, stale = patch.prepare("1.0.3", root=root, sources=[f"{base}/stale", f"{base}/fresh"])
    assert (root / patch.STAGING / "AramHex.exe").read_bytes() == b"exe-new"
    assert len(changed) == 3 and stale == ["_internal/lolhex/data/gone.json"]


def test_download_rejects_tampered_file(tmp_path, server):
    srv, base = server
    _publish(srv, "u", NEW, "1.0.3")
    (srv / "u" / "files" / "AramHex.exe").write_bytes(b"evil!!!")
    (srv / "u" / "files" / "AramHex.exe.gz").write_bytes(__import__("gzip").compress(b"evil!!!"))
    root = _tree(tmp_path / "app", OLD)
    with pytest.raises(patch.NeedFullInstall, match="AramHex.exe"):
        patch.prepare("1.0.3", root=root, sources=[f"{base}/u"])
    assert not (root / patch.STAGING).exists()  # 校验不过：暂存目录清掉


win = pytest.mark.skipif(sys.platform != "win32", reason="替换脚本是 Windows PowerShell")


def _done_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


@win
def test_apply_script_replaces_files(tmp_path):
    root = _tree(tmp_path / "应用 目录", OLD)  # 中文和空格路径
    staged = {f"{patch.STAGING}/{k}": v for k, v in NEW.items() if k != "_internal/cv2/cv2.pyd" and NEW[k] != OLD.get(k)}
    _tree(root, staged)
    changed = [k[len(patch.STAGING) + 1:] for k in staged]
    patch.launch_apply(root, changed, ["_internal/lolhex/data/gone.json"], "1.0.3", pid=_done_pid(), wait=True)
    assert (root / "AramHex.exe").read_bytes() == b"exe-new"
    assert (root / "_internal/lolhex/data/b.json").read_bytes() == b"b"
    assert not (root / "_internal/lolhex/data/gone.json").exists()
    assert (root / "_internal/cv2/cv2.pyd").read_bytes() == b"opencv"
    assert not (root / patch.STAGING).exists() and not (root / patch.BACKUP).exists()
    assert not (root / "update-failed.txt").exists()


@win
def test_apply_script_rolls_back_on_failure(tmp_path):
    root = _tree(tmp_path / "app", OLD)
    _tree(root, {f"{patch.STAGING}/AramHex.exe": b"exe-new"})  # a.json 的新文件缺了：替换到一半失败
    patch.launch_apply(root, ["AramHex.exe", "_internal/lolhex/data/a.json"], ["_internal/lolhex/data/gone.json"],
                       "1.0.3", pid=_done_pid(), wait=True)
    for rel, body in OLD.items():
        assert (root / rel).read_bytes() == body, rel  # 全部还原成旧版本
    assert (root / "update-failed.txt").exists()

def test_gz_preferred_and_plain_fallback(tmp_path, server):
    """新清单优先下 .gz（jsDelivr 不给下 .exe）；.gz 坏了就下原文件，照样校验。"""
    import gzip
    srv, base = server
    _publish(srv, "u", NEW, "1.0.3")
    assert (srv / "u" / "files" / "AramHex.exe.gz").exists() and (srv / "u" / "files" / "AramHex.exe").exists()
    assert gzip.decompress((srv / "u" / "files" / "AramHex.exe.gz").read_bytes()) == b"exe-new"
    (srv / "u" / "files" / "AramHex.exe.gz").write_bytes(b"not gzip")
    root = _tree(tmp_path / "app", OLD)
    patch.prepare("1.0.3", root=root, sources=[f"{base}/u"])
    assert (root / patch.STAGING / "AramHex.exe").read_bytes() == b"exe-new"

def test_dist_info_metadata_is_patchable(tmp_path):
    """两次正式构建之间，第三方库的安装记录（dist-info/RECORD）会变：不能因此要求完整安装包。"""
    old = {**OLD, "_internal/numpy-2.5.3.dist-info/RECORD": b"a"}
    new = {**NEW, "_internal/numpy-2.5.3.dist-info/RECORD": b"b"}
    m = patch.build_manifest(_tree(tmp_path / "new", new), "1.0.3")
    changed, _ = patch.plan(m, _tree(tmp_path / "old", old))
    assert "_internal/numpy-2.5.3.dist-info/RECORD" in changed

def test_prepare_reports_phases_and_progress(tmp_path, server):
    srv, base = server
    _publish(srv, "u", NEW, "1.0.3")
    m = json.loads((srv / "u" / "manifest.json").read_text(encoding="utf-8"))
    assert set(m["packed"]) == set(m["patchable"])  # 清单记了压缩后大小，进度按它算
    phases, seen = [], []
    patch.prepare("1.0.3", root=_tree(tmp_path / "app", OLD), sources=[f"{base}/u"],
                  phase=phases.append, progress=lambda d, t: seen.append((d, t)))
    assert phases == ["检查更新文件…", "比对本机文件…", "下载更新…"]
    total = sum(m["packed"][r] for r in ("AramHex.exe", "_internal/lolhex/data/a.json", "_internal/lolhex/data/b.json"))
    assert seen and seen[-1] == (total, total) and all(d <= t for d, t in seen)
