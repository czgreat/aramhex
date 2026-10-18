"""把统计数据仓库里最新的一份数据放进 lolhex/data/bundled/（随安装版和便携版内置）。

    python scripts/update_data.py                      # 从 GitHub 下载 czgreat/aramhex-data 的最新数据
    python scripts/update_data.py --from ../aramhex-data   # 用本地克隆

需要认证时，设置环境变量 GITHUB_TOKEN。
每个文件都按 latest.json 里的 SHA-256 校验；校验不过就不改 bundled/。
内置数据比要换的新（例如上线前手工放进去的过渡数据）就不换，免得发版时被覆盖。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lolhex.data.public import RAW_BASE, Dataset, check_manifest  # noqa: E402

BUNDLED = ROOT / "lolhex" / "data" / "bundled"


def _get(url: str) -> bytes:
    import requests
    headers = {"User-Agent": "aramhex-build"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    return r.content


def _when(value):
    from datetime import datetime
    return datetime.fromisoformat(str(value))


def _bundled_snapshot():
    """现在内置数据的样本时间；没有或读不出来返回 None。"""
    try:
        return _when(json.loads((BUNDLED / "latest.json").read_text(encoding="utf-8"))["snapshot_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="更新内置统计数据")
    ap.add_argument("--from", dest="src", type=Path, help="本地 aramhex-data 目录（不填则从 GitHub 下载）")
    args = ap.parse_args(argv)
    if args.src:
        manifest = json.loads((args.src / "latest.json").read_text(encoding="utf-8"))
        read = lambda name: (args.src / manifest["path"] / name).read_bytes()  # noqa: E731
    else:
        manifest = json.loads(_get(f"{RAW_BASE}/latest.json"))
        read = lambda name: _get(f"{RAW_BASE}/{manifest['path']}/{name}")  # noqa: E731
    check_manifest(manifest)
    current = _bundled_snapshot()
    if current and _when(manifest.get("snapshot_at")) <= current:
        print(f"内置数据（样本截至 {current:%Y-%m-%d %H:%M}）不比 {manifest.get('snapshot_at')} 的旧，不换")
        return 0
    tmp = BUNDLED.with_name("bundled.new")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        for name, f in manifest["files"].items():
            body = read(name)
            if hashlib.sha256(body).hexdigest() != f["sha256"]:
                raise SystemExit(f"{name} 的 SHA-256 和清单对不上")
            (tmp / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp / name).write_bytes(body)
        (tmp / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                         encoding="utf-8")
        ds = Dataset(tmp, "bundled")
        shutil.rmtree(BUNDLED, ignore_errors=True)
        tmp.rename(BUNDLED)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"内置数据已更新：{ds.patch}，样本截至 {ds.snapshot_at}，{len(manifest['files'])} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
