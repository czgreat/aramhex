"""发版前检查内置资料，发布流程里在打包前运行；不通过就不发版。

    python scripts/check_release_data.py

- 来源：内置英雄资料不能有腾讯装备榜、海克斯评级、搭档推荐和基础鞋。
- 批准：内置统计不能还标着待审。待审数据只能进测试包，不能进正式版。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.mix_recommendations import recommendation_residue  # noqa: E402

DATA = ROOT / 'lolhex' / 'data'
PENDING_MARKS = ('not_approved', 'not authorized')


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def source_problems(data: Path = DATA) -> list[str]:
    heroes, static = _read(data / 'hero_bundled.json'), _read(data / 'static_bundled.json')
    latest = _read(data / 'bundled' / 'latest.json')
    found = [f'内置英雄资料残留 {key}：{count} 行'
             for key, count in recommendation_residue(heroes['heroes']).items() if count]
    if static.get('patch') != latest.get('patch'):
        found.append(f"内置官方资料 {static.get('patch')} 和内置统计 {latest.get('patch')} 版本不一致")
    return found


def _pending_marks(value, where: str):
    """翻遍整个文件，找还写着待审的地方；一处批准不能盖住另一处待审。"""
    if isinstance(value, dict):
        for key, child in value.items():
            if key.endswith('approved') and child is not True:
                yield f'{where}.{key}={child}'
            yield from _pending_marks(child, f'{where}.{key}')
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _pending_marks(child, f'{where}[{index}]')
    elif isinstance(value, str) and any(mark in value.lower() for mark in PENDING_MARKS):
        yield f'{where}={value}'


def approval_problems(data: Path = DATA) -> list[str]:
    latest = _read(data / 'bundled' / 'latest.json')
    found = []
    if latest.get('seq_is_provisional'):
        found.append(f"内置统计 seq {latest.get('seq')} 还是占位序号")
    for name, row in (('latest.json', latest), ('meta.json', _read(data / 'bundled' / 'meta.json')),
                      ('hero_bundled.json', _read(data / 'hero_bundled.json'))):
        found += [f'内置数据仍标着待审：{mark}' for mark in _pending_marks(row, name)]
    return found


def main() -> int:
    found = source_problems() + approval_problems()
    for line in found:
        print(line, file=sys.stderr)
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
