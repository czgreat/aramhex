"""整理发布用的英雄资料：去掉腾讯海克斯评级和搭档推荐，装备来源由调用方决定。

    python scripts/mix_recommendations.py --static 静态资料.json --heroes 腾讯英雄资料.json --output 合并后.json
    python scripts/mix_recommendations.py --heroes 腾讯英雄资料.json --core core.json --shoes shoes.json --output 合并后.json

第一种：出装用腾讯（1.1.5 的做法）。第二种：出装用自采。
腾讯原始资料带海克斯评级。发布前必须先过这一步，再交给 official_data.py candidate，
否则这些字段会随资料包回到客户端。
"""
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lolhex.panel import by_win  # noqa: E402

BASE_BOOTS = frozenset({'1001'})  # 基础鞋不推荐，鞋子栏只显示二级鞋。


def self_collected_recommendations(payload: dict, core: dict, shoes: dict) -> dict:
    """输入/输出均为 official payload；缺自采推荐的英雄显式留空。"""
    mixed = deepcopy(payload)
    heroes = mixed['heroes']['heroes']
    if (set(core) | set(shoes)) - set(heroes):
        raise ValueError('自采装备包含英雄资料中不存在的英雄')
    for hid, hero in heroes.items():
        # 英雄海克斯行的 tier/rank 会触发腾讯评级兜底；推荐只取公开自采表。
        hero['augments'] = []
        hero['partners'] = []
        hero['builds'] = {
            'core': deepcopy(core.get(hid, [])),
            'shoes': [deepcopy(row) for row in shoes.get(hid, []) if str(row.get('item')) not in BASE_BOOTS],
            'start': [],
            'first': [],
            'full': [],
        }
    return mixed


def tencent_recommendations(payload: dict, items: dict) -> dict:
    """出装用腾讯，海克斯评级和搭档推荐照样去掉。

    1.1.5 客户端按数据顺序显示三件套，所以把 1.1.4 客户端做的筛选和胜率排序提前到这里，并标 ordered，
    显示效果和 1.1.4 一样（箭头）。字典里没有的装备（自动升级形态）整条去掉，不让界面显示成数字。
    """
    mixed = deepcopy(payload)
    known = lambda row: all(str(i) in items for i in row.get('items') or [row.get('item')])
    for hero in mixed['heroes']['heroes'].values():
        builds = hero['builds']
        hero['augments'] = []
        hero['partners'] = []
        hero['builds'] = {
            'core': [{**row, 'ordered': True} for row in by_win([r for r in builds.get('core', []) if known(r)])],
            'shoes': [r for r in builds.get('shoes', []) if known(r) and str(r.get('item')) not in BASE_BOOTS],
            'start': [r for r in builds.get('start', []) if known(r)],
            'first': [],
            'full': [],
        }
    return mixed


def recommendation_residue(heroes: dict) -> dict:
    """统计英雄资料里不该发布的字段；全为 0 才符合来源政策。出门装来源由调用方决定，不算在内。"""
    builds = [hero.get('builds') or {} for hero in heroes.values()]
    return {
        'augments': sum(len(hero.get('augments') or []) for hero in heroes.values()),
        'partners': sum(len(hero.get('partners') or []) for hero in heroes.values()),
        'first': sum(len(b.get('first') or []) for b in builds),
        'full': sum(len(b.get('full') or []) for b in builds),
        'base_boots': sum(str(row.get('item')) in BASE_BOOTS for b in builds for row in b.get('shoes') or []),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--heroes', type=Path, required=True)
    parser.add_argument('--static', type=Path, help='出装用腾讯时必填，用来去掉字典里没有的装备')
    parser.add_argument('--core', type=Path, help='出装用自采时必填')
    parser.add_argument('--shoes', type=Path, help='出装用自采时必填')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.exit(1, '输出文件已存在，请换一个新路径\n')
    def read(path):
        return json.loads(path.read_text(encoding='utf-8'))
    if args.static and not (args.core or args.shoes):
        mixed = tencent_recommendations({'heroes': read(args.heroes)}, read(args.static)['items'])
    elif args.core and args.shoes and not args.static:
        mixed = self_collected_recommendations({'heroes': read(args.heroes)}, read(args.core), read(args.shoes))
    else:
        parser.exit(1, '要么给 --static（腾讯出装），要么同时给 --core 和 --shoes（自采出装）\n')
    args.output.write_text(json.dumps(mixed['heroes'], ensure_ascii=False, sort_keys=True, separators=(',', ':')),
                           encoding='utf-8')
    print(json.dumps(recommendation_residue(mixed['heroes']['heroes'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
