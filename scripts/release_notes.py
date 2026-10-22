"""生成 GitHub Release 说明（给玩家看，程序里的"有新版本"提示也显示它）：
CHANGELOG.md 里这一版写了什么。只写更新了什么、修了什么，不写技术细节。

    python scripts/release_notes.py 1.0.7 > build/release-notes.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def notes(version: str, changelog: str) -> str:
    m = re.search(rf"(?ms)^## {re.escape(version)}\s*$(.*?)(?=^## |\Z)", changelog)
    body = m.group(1).strip() if m else f"AramHex {version}"
    # 软件里「有新版本」横条只显示前 200 字：发布说明只放关键几条，「完整更新内容：」以下留在 CHANGELOG.md
    body = body.split("完整更新内容：")[0].strip()
    return body + "\n"  # 只写这次更新了什么；下载文件发布页下面自己列着


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    sys.stdout.write(notes(argv[0].lstrip("vV"), (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
