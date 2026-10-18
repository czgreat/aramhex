"""自采统计数据：内置 / 下载两份里挑样本新的；逐文件校验；分段切换；从数据仓库下载更新。"""

import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import make_dataset
from lolhex.data import public
from lolhex.data.public import Dataset, DatasetError, PublicStore, check_manifest


@pytest.fixture
def server(tmp_path):
    """本地 HTTP 服务，模拟 raw.githubusercontent.com/<数据仓库>/main/。"""
    root = tmp_path / "remote"
    root.mkdir()
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _publish(root, snapshot_at, patch="16.20"):
    """按数据仓库的目录结构放一份：latest.json 在根目录，文件在 patches/<版本>/。"""
    folder = root / "patches" / patch
    manifest = make_dataset(folder, snapshot_at=snapshot_at, patch=patch)
    (folder / "latest.json").unlink()
    (root / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_bundled_dataset_loads_and_ranks(store):
    st = store.status()
    assert st["dataset"] and st["dataset_patch"] == "16.20" and st["dataset_origin"] == "bundled"
    assert st["bands_available"] and st["dataset_label"] == "自采统计·16.20版本"
    assert st["dataset_matches"] == 70000 and st["dataset_collected"] == 72000
    assert store.ranks["heroes"]["17"]["rank"] == 1 and store.ranks["heroes"]["17"]["games"] == 400000
    assert store.ranks["augments"]["1029"]["games"] == 45000  # 所有英雄合计


def test_band_switch_and_fallback(store):
    store.set_band("casual")
    assert store.win_data("17")["band_label"] == "娱乐"
    store.set_band("high")
    assert store.effective_band() == "high" and store.ranks["heroes"]["17"]["games"] == 2000
    assert store.win_data("17")["band_label"] == "高分"
    store.set_band("bogus")
    assert store.effective_band() is None and store.ranks["heroes"]["17"]["games"] == 400000


def test_dataset_without_bands_falls_back_to_all(tmp_path):
    make_dataset(tmp_path / "b", bands=False)
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", check_updates=False)
    s.set_band("top")
    assert s.band == "top" and s.effective_band() is None and not s.bands_available()


def test_tampered_file_is_rejected(tmp_path):
    make_dataset(tmp_path / "b")
    (tmp_path / "b" / "heroes.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DatasetError):
        Dataset(tmp_path / "b", "bundled")
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", check_updates=False)
    assert s.dataset is None and s.win_data("17") is None and "暂无" in s.label()


@pytest.mark.parametrize("bad", ["../evil.json", "/abs.json", "x.exe"])
def test_manifest_rejects_unsafe_names(bad):
    with pytest.raises(DatasetError):
        check_manifest({"schema_version": 1, "files": {"meta.json": {"sha256": "0" * 64},
                                                       "heroes.json": {"sha256": "0" * 64},
                                                       "hero_augments.json": {"sha256": "0" * 64},
                                                       bad: {"sha256": "0" * 64}}})


def test_newer_download_wins_over_bundled(tmp_path):
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00")
    make_dataset(tmp_path / "data" / "public", snapshot_at="2026-10-20T00:00:00+08:00", patch="16.21")
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", check_updates=False)
    assert s.dataset.origin == "downloaded" and s.dataset.patch == "16.21"


def test_sync_downloads_newer_data(tmp_path, server):
    root, base = server
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00")
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=base)
    _publish(root, "2026-10-13T00:00:00+08:00")
    assert s.sync_data(force=True) is False  # 和内置的一样新：不下载
    _publish(root, "2026-10-20T00:00:00+08:00", patch="16.21")
    assert s.sync_data(force=True) is True
    assert s.dataset.origin == "downloaded" and s.dataset.patch == "16.21" and s.data_updated_at
    assert (tmp_path / "data" / "public" / "bands" / "top" / "heroes.json").exists()
    assert s.sync_data() is False  # 6 小时内不重复查


def test_sync_rejects_tampered_download(tmp_path, server):
    root, base = server
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00")
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=base)
    _publish(root, "2026-10-20T00:00:00+08:00", patch="16.21")
    (root / "patches" / "16.21" / "heroes.json").write_text("{}", encoding="utf-8")
    assert s.sync_data(force=True) is False
    assert s.dataset.origin == "bundled" and "校验" in s.sync_error
    assert not (tmp_path / "data" / "public").exists() and not (tmp_path / "data" / "public.new").exists()


def test_sync_failure_is_quiet(tmp_path):
    make_dataset(tmp_path / "b")
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url="http://127.0.0.1:9")
    assert s.sync_data(force=True) is False and s.sync_error and s.dataset.origin == "bundled"


def test_sync_disabled_only_offers(tmp_path, server):
    # 关了自动更新：只看清单，有新的就记到 data_offer 等用户点头，不下载；点头（manual）才下载
    root, base = server
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00")
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=base, check_updates=False)
    _publish(root, "2026-10-20T00:00:00+08:00", patch="16.21")
    assert s.sync_data(force=True) is False
    assert s.dataset.origin == "bundled" and not (tmp_path / "data" / "public").exists()
    assert s.data_offer and "16.21 版本" in s.data_offer["text"] and "截至 10-20" in s.data_offer["text"]
    assert s.status()["data_offer"] == s.data_offer
    assert s.sync_data(force=True, manual=True) is True
    assert s.dataset.patch == "16.21" and s.data_offer is None


def test_sync_waits_until_game_ends(tmp_path, server):
    # 对局中后台不换数据（一局里推荐前后一致），打完再换，换完记一条提示
    root, base = server
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00")
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=base)
    _publish(root, "2026-10-20T00:00:00+08:00", patch="16.21")
    in_game = [True]
    s.hold = lambda: in_game[0]
    assert s.sync_data() is False and s.dataset.origin == "bundled" and s._last_sync == 0
    in_game[0] = False
    assert s.sync_data() is True and s.dataset.patch == "16.21"
    assert s.data_note and s.data_note["text"].startswith("数据已更新：16.21 版本，截至 10-20")
    assert s.status()["data_note"]["id"] == s.dataset.revision


def test_real_bundled_data_is_valid():
    """仓库里随程序发布的内置数据：清单、校验、分段都完整。"""
    if not (public.BUNDLED_DIR / "latest.json").exists():
        pytest.skip("还没有放内置数据（scripts/update_data.py）")
    ds = Dataset(public.BUNDLED_DIR, "bundled")
    heroes, augs = ds.table(None)
    assert len(heroes) > 150 and ds.bands_available()
    assert all(0 < h["win_rate"] < 1 for h in heroes.values())


def test_sync_falls_back_to_mirror(tmp_path, server):
    """GitHub 连不上（国内常见）：换下一个镜像下载；第一个镜像数据被改过也会换下一个。"""
    root, base = server
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00")
    _publish(root, "2026-10-20T00:00:00+08:00", patch="16.21")
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=["http://127.0.0.1:9", base])
    assert s.sync_data(force=True) is True
    assert s.data_source == base and s.dataset.patch == "16.21"


def test_sync_skips_tampered_mirror(tmp_path, server):
    root, base = server
    bad = tmp_path / "remote" / "bad"
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00")
    _publish(root, "2026-10-20T00:00:00+08:00", patch="16.21")
    _publish(bad, "2026-10-20T00:00:00+08:00", patch="16.21")
    (bad / "patches" / "16.21" / "heroes.json").write_text("{}", encoding="utf-8")
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=[base + "/bad", base])
    assert s.sync_data(force=True) is True and s.data_source == base

def _publish_tag(root, seq, snapshot_at, patch="16.21"):
    """模拟 jsDelivr 按 tag 取：<root>/data-v<seq>/latest.json + patches/..."""
    folder = root / f"data-v{seq}"
    manifest = make_dataset(folder / "patches" / patch, snapshot_at=snapshot_at, patch=patch, seq=seq)
    (folder / "patches" / patch / "latest.json").unlink()
    (folder / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_github_down_probes_data_tags(tmp_path, server):
    """GitHub 连不上：按 data-v<序号+1>、+2… 试探 jsDelivr，取最新的一份（@main 的缓存是旧的）。"""
    root, base = server
    host = base.split("//")[1]
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00", seq=1)
    _publish_tag(root, 2, "2026-10-20T00:00:00+08:00")
    _publish_tag(root, 3, "2026-10-27T00:00:00+08:00", patch="16.22")
    _publish(root, "2026-10-13T00:00:00+08:00")  # "@main" 还是旧的（缓存）
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=["http://127.0.0.1:9", base],
                    tag_hosts=(host,), tag_fmt="http://{host}/data-v{seq}")
    assert s.sync_data(force=True) is True
    assert s.dataset.patch == "16.22" and s.dataset.seq == 3 and s.data_source.endswith("/data-v3")


def test_no_newer_tag_falls_back_to_branch(tmp_path, server):
    root, base = server
    host = base.split("//")[1]
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00", seq=5)
    _publish(root, "2026-10-20T00:00:00+08:00", patch="16.21")
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=["http://127.0.0.1:9", base],
                    tag_hosts=(host,), tag_fmt="http://{host}/data-v{seq}")
    assert s.sync_data(force=True) is True and s.data_source == base

def test_data_download_reports_progress(tmp_path, server):
    root, base = server
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00")
    manifest = _publish(root, "2026-10-20T00:00:00+08:00", patch="16.21")
    seen = []
    public.download(base, manifest, tmp_path / "dl", progress=lambda d, t: seen.append((d, t)))
    total = sum(f["size"] for f in manifest["files"].values())
    assert seen[-1] == (total, total)
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=base)
    assert s.status()["data_progress"] is None  # 不在下载时没有进度


def _normalize(root, patch):
    """把发布目录里的文件按数据仓库导出的写法重写一遍（胜率 = round(胜场/局数, 6)），重算清单。"""
    folder = root / "patches" / patch
    manifest = json.loads((root / "latest.json").read_text(encoding="utf-8"))
    for name in manifest["files"]:
        obj = json.loads((folder / name).read_text(encoding="utf-8"))
        if name.endswith("hero_augments.json"):
            for d in obj.values():
                for s in d.values():
                    s["win_rate"] = round(s["wins"] / s["games"], 6) if s["games"] else 0.0
                    s["low_sample"] = bool(s.get("low_sample"))
        body = public._dump_json(obj)
        (folder / name).write_bytes(body)
        manifest["files"][name] = {"sha256": public.hashlib.sha256(body).hexdigest(), "size": len(body)}
    (root / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder, manifest


def test_pack_download_restores_exact_files(tmp_path, server):
    root, base = server
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00")
    _publish(root, "2026-10-20T00:00:00+08:00", patch="16.21")
    folder, manifest = _normalize(root, "16.21")
    pack = public.make_pack(folder, manifest)
    (root / "patches" / "16.21.pack.xz").write_bytes(pack)
    # 压缩包可用时，逐个文件的地址都删掉：证明数据确实来自压缩包
    for name in manifest["files"]:
        (folder / name).rename(folder / (name + ".bak"))
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=base)
    assert s.sync_data(force=True) is True and s.dataset.patch == "16.21"
    assert len(pack) < sum(f["size"] for f in manifest["files"].values())


def test_bad_pack_falls_back_to_files(tmp_path, server):
    root, base = server
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00")
    _publish(root, "2026-10-20T00:00:00+08:00", patch="16.21")
    (root / "patches" / "16.21.pack.xz").write_bytes(b"not xz")
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=base)
    assert s.sync_data(force=True) is True and s.dataset.patch == "16.21"


def test_event_mode_uses_event_dataset(tmp_path, server):
    root, base = server
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00")
    m = _publish(root, "2026-10-13T00:00:00+08:00")
    ev_folder = root / "patches" / "16.20-fp"
    ev = make_dataset(ev_folder, snapshot_at="2026-10-15T00:00:00+08:00", bands=False)
    (ev_folder / "latest.json").unlink()
    ev.update(mode="first-prismatic", mode_label="首次必棱彩", path="patches/16.20-fp")
    (root / "index.json").write_text(json.dumps({"schema_version": 1, "datasets": [m, ev]}), encoding="utf-8")
    event = [None]
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=base, event_source=lambda: event[0])
    assert s.sync_data(force=True) is False and s.dataset.mode == "normal"
    event[0] = {"mode": "first-prismatic", "label": "首次必棱彩"}
    assert s.sync_data(force=True) is True and s.dataset.mode == "first-prismatic"
    st = s.status()
    assert st["dataset_mode_label"] == "首次必棱彩" and not st["event_fallback"]
    event[0] = {"mode": "gold-reroll", "label": "金色刷新"}  # 活动数据还没出：先用常规并提示
    s.sync_data(force=True)
    st = s.status()
    assert s.dataset.mode == "normal" and st["event_fallback"] and st["event_label"] == "金色刷新"
    from lolhex.ui.data_status import data_status_html
    assert "金色刷新的数据还没出" in data_status_html(st)
    event[0] = None  # 活动结束：回到常规
    s.sync_data(force=True)
    assert s.dataset.mode == "normal" and not s.status()["event_fallback"]
