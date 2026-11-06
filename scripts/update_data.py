"""把统计数据仓库里最新的一份数据放进 lolhex/data/bundled/（随安装版和便携版内置）。

    python scripts/update_data.py                      # 从自有服务器下载最新统计包
    python scripts/update_data.py --from ../aramhex-data   # 用本地克隆

每个文件都按 latest.json 里的 SHA-256 校验；校验不过就不改 bundled/。
内置数据比要换的新（例如上线前手工放进去的过渡数据）就不换，免得发版时被覆盖。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lolhex import cloud  # noqa: E402
from lolhex.data.public import Dataset, check_manifest, newer_manifest  # noqa: E402

BUNDLED = ROOT / "lolhex" / "data" / "bundled"


def _get(url: str) -> bytes:
    import requests
    headers = {"User-Agent": "aramhex-build"}
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    return r.content


def _bundled_dataset():
    """内置资料损坏时允许用校验完整的新包修复。"""
    try:
        return Dataset(BUNDLED, "bundled")
    except (OSError, ValueError, KeyError, TypeError):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="更新内置统计数据")
    ap.add_argument("--from", dest="src", type=Path, help="本地统计数据目录（不填则从自有服务器下载）")
    args = ap.parse_args(argv)
    if args.src:
        manifest = json.loads((args.src / "latest.json").read_text(encoding="utf-8"))
        read = lambda name: (args.src / manifest["path"] / name).read_bytes()  # noqa: E731
    else:
        sources = list(dict.fromkeys([u.rstrip("/") for u in cloud.mirrors("data")] +
                                     [f"{s.rstrip('/')}/u/data" for s in cloud.sites()]))
        errors = []
        for base in sources:
            try:
                manifest = check_manifest(json.loads(_get(f"{base}/latest.json")))
                break
            except Exception as exc:
                errors.append(f"{base}: {exc}")
        else:
            raise RuntimeError("自有服务器统计数据不可用：" + "; ".join(errors))
        read = lambda name: _get(f"{base}/{manifest['path']}/{name}")  # noqa: E731
    check_manifest(manifest)
    current = _bundled_dataset()
    if current and not newer_manifest(manifest, current):
        print(f"内置数据（样本截至 {current.snapshot_at}，seq {current.seq}）已是最新，不换")
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
