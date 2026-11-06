import json

import pytest

from scripts import check_release_data as check
from scripts.mix_recommendations import self_collected_recommendations


def test_committed_bundled_data_meets_source_policy():
    assert check.source_problems() == []


@pytest.fixture
def data(tmp_path):
    (tmp_path / 'bundled').mkdir()

    def write(name, obj):
        (tmp_path / name).write_text(json.dumps(obj), encoding='utf-8')

    write('static_bundled.json', {'patch': '16.22'})
    write('hero_bundled.json', {'heroes': {'17': {'augments': [], 'partners': [], 'builds': {
        'core': [], 'shoes': [{'item': '3020'}], 'first': [], 'full': [], 'start': []}}}})
    write('bundled/latest.json', {'patch': '16.22', 'seq': 14})
    write('bundled/meta.json', {'patch': '16.22'})
    return tmp_path


def test_clean_release_data_passes(data):
    assert check.source_problems(data) == [] and check.approval_problems(data) == []


def test_tencent_fields_and_base_boots_block_release(data):
    path = data / 'hero_bundled.json'
    obj = json.loads(path.read_text(encoding='utf-8'))
    hero = obj['heroes']['17']
    hero['augments'] = [{'id': '1', 'tier': 'S'}]
    hero['builds']['first'] = [{'item': '9'}]
    hero['builds']['shoes'].append({'item': '1001'})
    path.write_text(json.dumps(obj), encoding='utf-8')
    found = '\n'.join(check.source_problems(data))
    assert 'augments' in found and 'first' in found and 'base_boots' in found


def test_patch_mismatch_blocks_release(data):
    (data / 'static_bundled.json').write_text(json.dumps({'patch': '16.21'}), encoding='utf-8')
    assert check.source_problems(data)


@pytest.mark.parametrize('name,row', [
    ('latest.json', {'patch': '16.22', 'seq': 13, 'seq_is_provisional': True}),
    ('latest.json', {'patch': '16.22', 'release_status': 'local_review_only_not_approved'}),
    ('meta.json', {'status': 'local_review_only_not_approved'}),
])
def test_review_only_statistics_block_release(data, name, row):
    (data / 'bundled' / name).write_text(json.dumps(row), encoding='utf-8')
    assert check.approval_problems(data)


@pytest.mark.parametrize('row', [
    {'status': 'approved', 'components': {'status': 'local_review_only_not_approved'}},
    {'status': 'approved', 'components': {'minimum_approved': False}},
    {'release_status': 'approved', 'status': 'local_review_only_not_approved'},
    {'status': 'approved', 'components': {'limitations': ['Review only; production publication is not authorized.']}},
])
def test_one_approved_field_cannot_hide_a_pending_one(data, row):
    (data / 'bundled' / 'meta.json').write_text(json.dumps(row), encoding='utf-8')
    assert check.approval_problems(data)


def test_review_mark_in_hero_file_blocks_release(data):
    path = data / 'hero_bundled.json'
    obj = json.loads(path.read_text(encoding='utf-8'))
    obj['components'] = {'status': 'local_review_only_not_approved'}
    path.write_text(json.dumps(obj), encoding='utf-8')
    assert check.approval_problems(data)


def test_fully_approved_statistics_pass(data):
    row = {'status': 'approved', 'components': {'status': 'approved', 'minimum_approved': True}}
    (data / 'bundled' / 'meta.json').write_text(json.dumps(row), encoding='utf-8')
    assert check.approval_problems(data) == []


def test_tencent_recommendations_match_old_client_display():
    from scripts.mix_recommendations import tencent_recommendations
    core = [{'items': ['1', '2', '3'], 'pick': .5, 'win': .50},
            {'items': ['2', '1', '3'], 'pick': .2, 'win': .55},
            {'items': ['1', '2', '4'], 'pick': .01, 'win': .90},  # 出场太低，旧客户端也会筛掉
            {'items': ['1', '2', '3121'], 'pick': .4, 'win': .70}]  # 字典里没有的升级形态
    base = {'heroes': {'heroes': {'17': {
        'augments': [{'id': '1', 'tier': 'S'}], 'partners': [{'hero': '1'}], 'skills': ['keep'],
        'builds': {'core': core, 'shoes': [{'item': '1001', 'pick': .9, 'win': .6}, {'item': '3020', 'pick': .5, 'win': .5}],
                   'start': [{'items': ['1'], 'pick': .1, 'win': .5}], 'first': [{'item': '1'}], 'full': [{'items': ['1']}]}}}}}
    items = {str(i): {} for i in (1, 2, 3, 4, 3020)}
    hero = tencent_recommendations(base, items)['heroes']['heroes']['17']
    assert [r['items'] for r in hero['builds']['core']] == [['2', '1', '3'], ['1', '2', '3']]
    assert all(r['ordered'] is True for r in hero['builds']['core'])
    assert [r['item'] for r in hero['builds']['shoes']] == ['3020']
    assert hero['builds']['start'] == [{'items': ['1'], 'pick': .1, 'win': .5}]
    assert hero['builds']['first'] == [] and hero['builds']['full'] == []
    assert hero['augments'] == [] and hero['partners'] == [] and hero['skills'] == ['keep']


def test_base_boots_are_never_mixed_into_shoes():
    base = {'heroes': {'heroes': {'17': {'skills': []}}}}
    shoes = {'17': [{'item': '1001', 'games': 9000}, {'item': '3020', 'games': 8000}]}
    mixed = self_collected_recommendations(base, {}, shoes)
    assert [row['item'] for row in mixed['heroes']['heroes']['17']['builds']['shoes']] == ['3020']
