"""发版前检查内置资料，发布流程里在打包前运行；不通过就不发版。

    python scripts/check_release_data.py [--data <内置数据目录>]

不带 --data 查本检出的 lolhex/data；拿冻结好的程序目录出包时传它的 _internal/lolhex/data，查真正装进去的那份。
- 来源：内置英雄资料不能有腾讯装备榜、海克斯评级、搭档推荐和基础鞋。
- 批准：内置统计不能还标着待审。待审数据只能进测试包，不能进正式版。
- 外服：名字表（i18n.json）不能比内置官方资料旧，海斗每个模式的海克斯都要有台服、马来西亚服、英文、日文名字；
  上一个正式版（git 标签）已有的海克斯、英雄名字不能被改掉或删掉，确认要改的写进 FOREIGN_RENAMED。
  不通过就重跑 python scripts/update_i18n.py（见 docs/DEVELOPMENT.md「每次发版必做：外服名字表」）。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.mix_recommendations import recommendation_residue  # noqa: E402
from scripts.update_i18n import renamed, trad_collisions  # noqa: E402

DATA = ROOT / 'lolhex' / 'data'
FOREIGN_LANGS = ('zh_CN', 'zh_TW', 'zh_MY', 'en_US', 'ja_JP')
FOREIGN_POOLS = ('kiwi', 'kiwi_jade')  # 常规海斗、经典海斗
# 确认过没有名字可用的：{海克斯 ID: 缺的语言}。新增要写明原因
FOREIGN_EXEMPT = {
    '3176': FOREIGN_LANGS,     # 五杀乐队的内部标记，没有名字
    '1327': ('zh_MY',),        # 马来西亚服和 2076 同名「坚定不移」，只认成 2076
    '3263': ('zh_MY',),        # 同上
    '1339': ('zh_MY',),        # 「激光治疗术」会让召唤师技能「治疗术」过线，简中名已经能认出它
}
# 确认过要改、要删的已有名字：{('augments' 或 'heroes', ID, 语言): 原因}。没写在这里的改动一律不让发版
FOREIGN_RENAMED: dict[tuple[str, str, str], str] = {}
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


def _patch(value) -> tuple[int, ...]:
    """'16.23.1' → (16, 23)。"""
    parts = str(value or '').split('.')[:2]
    return tuple(int(p) for p in parts) if len(parts) == 2 and all(p.isdigit() for p in parts) else ()


def _hero_aliases(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [name for name in value if isinstance(name, str) and name.strip()]


def foreign_problems(data: Path = DATA) -> list[str]:
    table, static = _read(data / 'i18n.json'), _read(data / 'static_bundled.json')
    names, pools, found = table.get('augments') or {}, table.get('pools') or {}, []
    if not _patch(table.get('ddragon')) or _patch(table.get('ddragon')) < _patch(static.get('patch')):
        found.append(f"外服名字表还是 {table.get('ddragon')}，内置官方资料已经是 {static.get('patch')}：重跑 scripts/update_i18n.py")
    found += [f'外服名字表缺模式 {p}' for p in FOREIGN_POOLS if not pools.get(p)]
    # 名字表自己记的各模式 + 腾讯资料里的海斗海克斯，两边有一个列着就要有名字
    need = {aid for ids in pools.values() for aid in ids}
    need |= {aid for aid, a in static['augments'].items() if a.get('source') != 'cdragon'
             and any(m.startswith('KIWI') for m in a.get('modes') or [])}
    for aid in sorted(need, key=int):
        missing = [lang for lang in FOREIGN_LANGS
                   if not (names.get(aid) or {}).get(lang) and lang not in FOREIGN_EXEMPT.get(aid, ())]
        if missing:
            label = (static['augments'].get(aid) or names.get(aid) or {})
            found.append(f"海克斯 {aid}（{label.get('name') or label.get('zh_CN') or '?'}）外服名字缺 {'、'.join(missing)}")
    hero_names = table.get('heroes') or {}
    for hid, hero in (static.get('heroes') or {}).items():
        translations = hero_names.get(hid)
        if not isinstance(translations, dict):
            translations = {}
        missing = [lang for lang in FOREIGN_LANGS if not _hero_aliases(translations.get(lang))]
        if missing:
            found.append(f"英雄 {hid}（{hero.get('name') or '?'}）外服名字缺 {'、'.join(missing)}")

    items = [x['name'] for x in _read(data / 'bundled' / 'item_metadata.json').values()] if (
        data / 'bundled' / 'item_metadata.json').exists() else []
    heroes = [name for translations in hero_names.values() if isinstance(translations, dict)
              for lang in ('zh_CN', 'zh_MY') for name in _hero_aliases(translations.get(lang))]
    clash = set(trad_collisions(names, table.get('trad') or {}, items + heroes)) - set(table.get('trad_skip') or ())
    if clash:
        found.append(f"繁简统一会把装备或英雄名认成海克斯：{'、'.join(sorted(clash))}，重跑 scripts/update_i18n.py")
    return found


def _version(text: str) -> tuple[int, ...]:
    """'v1.2.0' → (1, 2, 0)；测试版、预发布等不是正式版的返回空。"""
    m = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', text.strip())
    return tuple(map(int, m.groups())) if m else ()


def _git(*args: str) -> str:
    return subprocess.run(['git', '-C', str(ROOT), *args], check=True, capture_output=True, encoding='utf-8').stdout


def changelog_versions() -> list[tuple[int, ...]]:
    """CHANGELOG.md 里的「## 1.2.0」标题；没有这个文件或一个版本都没有时抛出异常。"""
    text = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    found = [v for v in map(_version, re.findall(r'(?m)^##\s+(\S+)', text)) if v]
    if not found:
        raise RuntimeError('CHANGELOG.md 里没有版本标题')
    return found


def released_names(version: str) -> tuple[str, dict]:
    """(标签, 名字表)：不比 version 新的最近一个正式版，正在发的这一版（标签就打在当前提交上）不算。
    没有 git 或没取标签时抛出异常，不能当作没问题。"""
    now, here = _version(version), set(_git('tag', '--points-at', 'HEAD').split())
    tags = [t for t in _git('tag', '--list', 'v*').split()
            if t not in here and _version(t) and (not now or _version(t) <= now)]
    if not tags:
        raise RuntimeError('没有以前的正式版标签')
    tag = max(tags, key=_version)
    # 只看本地标签会漏：没取到最近的标签，就会拿更早的版本比。CHANGELOG 里上一个版本的标签必须在
    last = max((v for v in changelog_versions() if not now or v < now), default=())
    if _version(tag) < last:
        raise RuntimeError(f"CHANGELOG 里上一个版本是 {'.'.join(map(str, last))}，本地最近的标签只有 {tag}，标签没取全")
    return tag, json.loads(_git('show', f'{tag}:lolhex/data/i18n.json'))


def rename_problems(old: dict, new: dict, tag: str) -> list[str]:
    """手改 i18n.json 也拦得住：和上一个正式版逐条比，已有的名字不能变、不能少。"""
    return [f'{"海克斯" if kind == "augments" else "英雄"} {aid} 的 {lang} 名字从 {tag} 的 {before} 变成 {after or "没有"}：'
            '查清原因；确实要改就写进 FOREIGN_RENAMED'
            for kind in ('augments', 'heroes') for aid, lang, before, after in renamed(old.get(kind) or {}, new.get(kind) or {})
            if (kind, aid, lang) not in FOREIGN_RENAMED]


def history_problems(data: Path = DATA) -> list[str]:
    from lolhex import __version__
    try:
        tag, old = released_names(__version__)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as e:
        return [f'取不到上一个正式版的名字表，没法核对外服名字有没有被改：{e}。在带标签的 git 仓库里跑（git fetch --tags）']
    return rename_problems(old, _read(data / 'i18n.json'), tag)


def approval_problems(data: Path = DATA) -> list[str]:
    latest = _read(data / 'bundled' / 'latest.json')
    found = []
    if latest.get('seq_is_provisional'):
        found.append(f"内置统计 seq {latest.get('seq')} 还是占位序号")
    for name, row in (('latest.json', latest), ('meta.json', _read(data / 'bundled' / 'meta.json')),
                      ('hero_bundled.json', _read(data / 'hero_bundled.json'))):
        found += [f'内置数据仍标着待审：{mark}' for mark in _pending_marks(row, name)]
    return found


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description='发版前检查内置资料')
    ap.add_argument('--data', type=Path, default=DATA, help='内置数据目录，默认本检出的 lolhex/data')
    data = ap.parse_args(argv).data
    if not (data / 'bundled' / 'latest.json').is_file():
        print(f'{data} 不是内置数据目录（没有 bundled/latest.json）', file=sys.stderr)
        return 1
    found = source_problems(data) + foreign_problems(data) + history_problems(data) + approval_problems(data)
    for line in found:
        print(line, file=sys.stderr)
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
