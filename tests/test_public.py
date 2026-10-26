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
    assert store.win_data("17")["band_label"] == "前 5%"
    store.set_band("top")  # 1.1.0 以前的「顶尖」：当前 5%（high）
    assert store.effective_band() == "high"
    store.set_band("bogus")
    assert store.effective_band() is None and store.ranks["heroes"]["17"]["games"] == 400000


def test_dataset_without_bands_falls_back_to_all(tmp_path):
    make_dataset(tmp_path / "b", bands=False)
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", check_updates=False)
    s.set_band("high")
    assert s.band == "high" and s.effective_band() is None and not s.bands_available()


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


def test_manual_sync_also_waits_during_game(tmp_path, server):
    root, base = server
    make_dataset(tmp_path / "b")
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=base)
    _publish(root, "2026-10-20T00:00:00+08:00", patch="16.21")
    s.hold = lambda: True
    assert not s.sync_data(force=True, manual=True)
    assert s.dataset.origin == "bundled" and s._last_sync == 0


def test_download_entering_game_stages_without_replacing_active_files(tmp_path, server, monkeypatch):
    root, base = server
    make_dataset(tmp_path / "b")
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=base)
    _publish(root, "2026-10-20T00:00:00+08:00", patch="16.21")
    assert s.sync_data(force=True)
    original_dataset = s.dataset
    original_manifest = (s.downloaded / "latest.json").read_bytes()
    original_download = public.download
    in_game = [False]
    s.hold = lambda: in_game[0]
    _publish(root, "2026-10-21T00:00:00+08:00", patch="16.21")

    def enter_during_download(*args, **kwargs):
        original_download(*args, **kwargs)
        in_game[0] = True

    monkeypatch.setattr(public, "download", enter_during_download)
    assert not s.sync_data(force=True)
    assert s.dataset is original_dataset
    assert (s.downloaded / "latest.json").read_bytes() == original_manifest
    assert not s.reload_dataset()
    in_game[0] = False
    assert s.sync_data()  # 仍在检查冷却内，也能启用已完整下载的资料。
    assert s.dataset.snapshot_at == "2026-10-21T00:00:00+08:00"


def test_real_bundled_data_is_valid():
    """仓库里随程序发布的内置数据：清单、校验完整；分段有没有和清单声明一致（4.0 首版只有全体，bands=false）。"""
    if not (public.BUNDLED_DIR / "latest.json").exists():
        pytest.skip("还没有放内置数据（scripts/update_data.py）")
    ds = Dataset(public.BUNDLED_DIR, "bundled")
    heroes, augs = ds.table(None)
    assert len(heroes) > 150 and ds.bands_available() == bool(ds.manifest.get("bands"))
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


def test_old_top_band_setting_becomes_high(tmp_path):
    # 1.1.1 去掉「顶尖」：老设置文件 data_band=top 读成 high
    import json
    from lolhex.config import Settings
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"data_band": "top", "install_id": "0123456789abcdef"}), encoding="utf-8")
    assert Settings.load(p).data_band == "high"


def _add_file(folder, name, obj):
    import hashlib
    import json as _json
    p = folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    m = _json.loads((folder / "latest.json").read_text(encoding="utf-8"))
    m["files"][name] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "size": p.stat().st_size}
    (folder / "latest.json").write_text(_json.dumps(m), encoding="utf-8")


def test_optional_spells_and_items_files(store):
    # 1.1.1（A12）：数据里有 hero_spells.json / hero_items.json 就显示，没有就跳过
    from lolhex.data.public import Dataset
    from lolhex.panel import hero_panel
    assert store.hero_spells("17") == ([], store.label()) and hero_panel(store, "17")["summoners"] == []
    folder = store.dataset.folder
    _add_file(folder, "hero_spells.json", {"17": {"4+32": {"games": 90000, "wins": 48000}, "4+7": {"games": 10000, "wins": 4000},
                                                  "坏": {"games": "x"}, "4+14": {"games": 10, "wins": 11}}})
    _add_file(folder, "hero_items.json", {"17": {"3115": {"games": 500, "wins": 270}}})
    store.dataset = Dataset(folder, store.dataset.origin)
    rows, label = store.hero_spells("17")
    assert [r["spells"] for r in rows] == [["4", "32"], ["4", "7"]] and abs(rows[0]["pick"] - 0.9) < 1e-9
    d = hero_panel(store, "17")
    assert len(d["summoners"]) == 2 and d["summoner_source"] and d["summoners"][0]["spells"][0]["id"] == "4"
    assert d["item_stats"][0]["item"]["id"] == "3115" and abs(d["item_stats"][0]["win"] - 0.54) < 1e-9
    from lolhex.ui import buildview
    html = "<br>".join(buildview.lines(d, None, True))
    assert "召唤师技能" in html and "常出装备" in html


def test_summoners_pick_over_3pct_1000_games_sorted_by_win_top3():
    """召唤师技能：出场 ≥3% 且 ≥1000 局，按胜率排，最多三组；都不够时放出场最多的一组（用户 10-23）。"""
    from lolhex.panel import sort_summoners
    rows = [{"spells": ["4", "32"], "games": 5530, "win": 0.456, "pick": 0.553},
            {"spells": ["4", "21"], "games": 3480, "win": 0.433, "pick": 0.348},
            {"spells": ["4", "6"], "games": 1200, "win": 0.47, "pick": 0.12},
            {"spells": ["4", "7"], "games": 440, "win": 0.60, "pick": 0.044},   # 局数不够 1000
            {"spells": ["1", "4"], "games": 200, "win": 0.70, "pick": 0.02}]    # 出场不够 3%
    assert [r["spells"][1] for r in sort_summoners(rows)] == ["6", "32", "21"]
    # 格温（seq8）：疾跑+雪球出场 5%、胜率最高，以前被 10% 挡掉
    gwen = [{"spells": ["4", "32"], "games": 186814, "win": 0.5712, "pick": 0.829},
            {"spells": ["4", "6"], "games": 24147, "win": 0.5566, "pick": 0.107},
            {"spells": ["6", "32"], "games": 11240, "win": 0.5877, "pick": 0.050}]
    assert [r["spells"] for r in sort_summoners(gwen)] == [["6", "32"], ["4", "32"], ["4", "6"]]
    few = [{"spells": ["4", "x"], "games": 90, "win": 0.5, "pick": 0.09}, {"spells": ["4", "y"], "games": 80, "win": 0.7, "pick": 0.08}]
    assert [r["spells"][1] for r in sort_summoners(few)] == ["x"]


def test_overlay_summoners_show_win_rate():
    """浮窗和主界面：胜率在前、带出场，一组一行；关了「显示胜率」只写出场。"""
    from lolhex.ui import buildview
    d = {"summoners": [{"spells": [{"id": "4", "name": "闪现"}, {"id": "32", "name": "雪球"}], "games": 900, "win": 0.534, "pick": 0.9},
                       {"spells": [{"id": "4", "name": "闪现"}, {"id": "6", "name": "疾跑"}], "games": 100, "win": 0.51, "pick": 0.1}],
         "summoner_source": "自采统计"}
    compact = buildview.lines(d, None, True, compact=True)
    rows = [x for x in compact if "闪现" in x]
    assert len(rows) == 2 and "胜率 53.4% · 出场 90.0%" in rows[0] and "胜率 51.0% · 出场 10.0%" in rows[1]
    off = "".join(buildview.lines(d, None, False, compact=True))
    assert "胜率" not in off and "出场 90.0%" in off
    full = "".join(buildview.lines(d, None, True))
    assert "胜率 53.4% · 出场 90.0%" in full


def test_second_update_does_not_keep_old_copies(tmp_path, server):
    """连着换两次数据：旧的一份换成功就删掉，data 目录里不越攒越多。"""
    root, base = server
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00")
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=base)
    _publish(root, "2026-10-20T00:00:00+08:00", patch="16.21")
    assert s.sync_data(force=True) is True
    _publish(root, "2026-10-22T00:00:00+08:00", patch="16.21")
    assert s.sync_data(force=True) is True
    assert s.dataset.snapshot_at.startswith("2026-10-22")
    assert sorted(p.name for p in (tmp_path / "data").iterdir() if p.name.startswith("public")) == ["public"]


def test_broken_pending_download_is_dropped(tmp_path, server):
    """暂存目录坏了（比如下载到一半断电）：删掉重下，不能每次都卡在它上面。"""
    root, base = server
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00")
    pending = tmp_path / "data" / "public-pending"
    make_dataset(pending, snapshot_at="2026-10-18T00:00:00+08:00", patch="16.21")
    (pending / "heroes.json").write_text("{}", encoding="utf-8")  # 和清单里的 SHA-256 对不上
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=base)
    _publish(root, "2026-10-20T00:00:00+08:00", patch="16.21")
    assert s.sync_data(force=True) is True
    assert not pending.exists() and s.dataset.snapshot_at.startswith("2026-10-20")


def test_manual_check_reports_activated_pending_data(tmp_path, server):
    """上次下载好、等对局结束才换的数据，手动检查时换上了，远端又没有更新的：要报「换了」，不能说已是最新。"""
    root, base = server
    make_dataset(tmp_path / "b", snapshot_at="2026-10-13T00:00:00+08:00")
    make_dataset(tmp_path / "data" / "public-pending", snapshot_at="2026-10-20T00:00:00+08:00", patch="16.21")
    _publish(root, "2026-10-20T00:00:00+08:00", patch="16.21")
    s = PublicStore(tmp_path / "data", bundled=tmp_path / "b", base_url=base)
    assert s.sync_data(force=True, manual=True) is True
    assert s.dataset.snapshot_at.startswith("2026-10-20")
