"""Managed mode must use the current reviewed icon snapshot after an upgrade."""
import base64

from lolhex.data import icons


def test_managed_icons_replace_previous_build_cache(tmp_path, monkeypatch):
    expected = b"new reviewed icon"
    monkeypatch.setattr(icons, "bundled_icons", lambda: {"item": {"2138": base64.b64encode(expected).decode()}})
    path = tmp_path / "item" / "2138.png"
    path.parent.mkdir()
    path.write_bytes(b"previous build icon")
    cache = icons.IconCache(tmp_path, allow_network=False)
    try:
        assert cache.local("item", "2138", None) == path.as_posix()
        assert path.read_bytes() == expected
        version, modified = cache.version, path.stat().st_mtime_ns
        assert cache.local("item", "2138", None) == path.as_posix()
        assert (cache.version, path.stat().st_mtime_ns) == (version, modified)
        assert not cache._pool._threads
    finally:
        cache._pool.shutdown()


def test_unmanaged_icons_keep_fresh_network_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(icons, "bundled_icons", lambda: {"item": {"2138": base64.b64encode(b"bundled").decode()}})
    path = tmp_path / "item" / "2138.png"
    path.parent.mkdir()
    path.write_bytes(b"newer downloaded icon")
    cache = icons.IconCache(tmp_path, allow_network=True)
    try:
        assert cache.local("item", "2138", "https://example.invalid/icon.png") == path.as_posix()
        assert path.read_bytes() == b"newer downloaded icon"
    finally:
        cache._pool.shutdown()


def test_real_updated_elixirs_replace_cached_old_bytes_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(icons.IconCache, "_fetch", lambda *a: (_ for _ in ()).throw(AssertionError("Network")))
    cache = icons.IconCache(tmp_path, allow_network=False)
    try:
        for key in ("2138", "2139", "2140"):
            path = tmp_path / "item" / f"{key}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"old icon")
            cache.local("item", key, None)
            assert path.read_bytes() == base64.b64decode(icons.bundled_icons()["item"][key])
    finally:
        cache._pool.shutdown()
