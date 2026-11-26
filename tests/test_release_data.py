import json

import pytest

from scripts import check_release_data as check
from scripts.mix_recommendations import self_collected_recommendations


def test_committed_bundled_data_meets_source_policy():
    assert check.source_problems() == []


def test_committed_foreign_names_cover_every_mode():
    """外服名字表漏更新时测试和发版都过不了：重跑 scripts/update_i18n.py。"""
    assert check.foreign_problems() == []


@pytest.fixture
def foreign(tmp_path):
    full = dict.fromkeys(check.FOREIGN_LANGS, '名字')

    def write(i18n=None, static=None):
        table = {'ddragon': '16.23.1', 'pools': {'kiwi': ['1'], 'kiwi_jade': ['2']}, 'augments': {'1': full, '2': full}}
        (tmp_path / 'i18n.json').write_text(json.dumps({**table, **(i18n or {})}), encoding='utf-8')
        (tmp_path / 'static_bundled.json').write_text(json.dumps(
            {'patch': '16.23', 'augments': {'1': {'name': '甲', 'modes': ['KIWI']}}, **(static or {})}), encoding='utf-8')
        return check.foreign_problems(tmp_path)
    write.full = full
    return write


def test_foreign_names_gate(foreign):
    assert foreign() == []
    assert foreign(static={'patch': '16.9'}) == []                                  # 按数字比，不按字符串比
    assert '16.22.1' in foreign(i18n={'ddragon': '16.22.1'})[0]                     # 1.2.0 就是这样漏的
    assert foreign(i18n={'ddragon': None})
    assert foreign(i18n={'pools': {'kiwi': ['1']}}) == ['外服名字表缺模式 kiwi_jade']   # 经典海斗漏了
    assert 'zh_TW' in foreign(i18n={'augments': {'1': foreign.full, '2': {k: v for k, v in foreign.full.items() if k != 'zh_TW'}}})[0]
    new = {'augments': {'9': {'name': '新海克斯', 'modes': ['KIWI_JADE']}}}
    assert '新海克斯' in foreign(static=new)[0]                                       # 腾讯新增、名字表还没有
    new['augments']['9']['source'] = 'cdragon'                                      # 没在任何模式里的旧海克斯不管
    assert foreign(static=new) == []
    clash = {'trad': {'戰': '战'}, 'augments': {'1': {**foreign.full, 'zh_TW': '狂戰士'}, '2': foreign.full},
             'heroes': {'2': {'zh_CN': ['狂战士', '奥拉夫']}}}
    assert '狂战士' in foreign(i18n=clash)[0]                                          # 繁简统一会认错英雄名
    assert foreign(i18n={**clash, 'trad_skip': ['狂战士']}) == []


def test_foreign_names_gate_requires_coverage_for_new_heroes(foreign):
    static = {'heroes': {'17': {'name': '新英雄'}}}
    translated = dict.fromkeys(check.FOREIGN_LANGS, ['名字'])

    assert foreign(static=static) == ['英雄 17（新英雄）外服名字缺 zh_CN、zh_TW、zh_MY、en_US、ja_JP']
    missing = {**translated, 'ja_JP': [' '], 'zh_MY': '名字'}
    assert foreign(i18n={'heroes': {'17': missing}}, static=static) == ['英雄 17（新英雄）外服名字缺 zh_MY、ja_JP']
    assert foreign(i18n={'heroes': {'17': translated}}, static=static) == []


def test_released_names_cannot_be_changed_by_hand(monkeypatch):
    old = {'augments': {'1': {'zh_CN': '甲', 'zh_TW': '乙'}}, 'heroes': {'2': {'zh_TW': ['奧拉夫', '狂戰士']}}}
    same = {'augments': {'1': {'zh_CN': '甲', 'zh_TW': '乙', 'zh_MY': '新补的'}, '9': {'zh_CN': '新海克斯'}}, 'heroes': old['heroes']}
    assert check.rename_problems(old, same, 'v1.2.0') == []
    changed = {'augments': {'1': {'zh_CN': '甲', 'zh_TW': '改了'}}, 'heroes': {}}
    got = check.rename_problems(old, changed, 'v1.2.0')
    assert len(got) == 2 and '改了' in got[0] and '英雄 2' in got[1]
    monkeypatch.setitem(check.FOREIGN_RENAMED, ('augments', '1', 'zh_TW'), '拳头改名')
    monkeypatch.setitem(check.FOREIGN_RENAMED, ('heroes', '2', 'zh_TW'), '测试')
    assert check.rename_problems(old, changed, 'v1.2.0') == []


def test_names_are_compared_with_the_previous_release_tag(tmp_path, monkeypatch):
    import subprocess
    git = lambda *a: subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-C', str(tmp_path), *a], check=True, capture_output=True)
    path = tmp_path / 'lolhex' / 'data' / 'i18n.json'
    path.parent.mkdir(parents=True)
    git('init', '-q')
    monkeypatch.setattr(check, 'ROOT', tmp_path)
    log = tmp_path / 'CHANGELOG.md'
    log.write_text('# 更新记录\n\n## 1.11.0\n\n## 1.10.0\n\n## 1.9.0\n', encoding='utf-8')
    for tag in ('v1.9.0', 'v1.10.0', 'v1.11.0-test.1', 'test-1', 'v1.11.0', 'v2.0.0'):
        path.write_text(json.dumps({'augments': {'1': {'zh_CN': tag}}}), encoding='utf-8')
        git('add', '-A')
        git('commit', '-q', '-m', tag)
        git('tag', tag)
        if tag == 'v1.11.0':  # 正在发的这一版：标签打在当前提交上，要和上一版比；按数字比，不按字符串比
            assert check.released_names('1.11.0') == ('v1.10.0', {'augments': {'1': {'zh_CN': 'v1.10.0'}}})
    path.write_text('{}', encoding='utf-8')
    git('commit', '-aqm', '之后的开发')
    assert check.released_names('1.11.0')[0] == 'v1.11.0' and check.released_names('1.10.5')[0] == 'v1.10.0'
    # 标签没取全（上一版 1.11.0 的标签不在本地）：不能拿更早的 1.10.0 比
    git('tag', '-d', 'v1.11.0')
    with pytest.raises(RuntimeError, match='1.11.0.*v1.10.0'):
        check.released_names('1.12.0')
    git('tag', 'v1.11.0', 'HEAD~2')
    assert check.released_names('1.12.0')[0] == 'v1.11.0'
    log.write_text('还没写', encoding='utf-8')
    with pytest.raises(RuntimeError, match='没有版本标题'):
        check.released_names('1.12.0')
    log.unlink()
    with pytest.raises(FileNotFoundError):
        check.released_names('1.12.0')
    log.write_text('## 1.11.0', encoding='utf-8')
    # 没取标签、没有 git：不能当作没问题
    git('tag', '-d', 'v1.9.0', 'v1.10.0', 'v1.11.0', 'v2.0.0')
    assert '取不到上一个正式版' in check.history_problems()[0]

    def no_git(*_):
        raise FileNotFoundError('git')
    monkeypatch.setattr(check, '_git', no_git)
    assert '取不到上一个正式版' in check.history_problems()[0]


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


def test_cli_checks_the_given_data_directory(data, capsys, monkeypatch):
    # 外服名字表的检查另有用例；这里只核对 --data 也传给了它们
    seen = []
    monkeypatch.setattr(check, 'foreign_problems', lambda d: seen.append(d) or [])
    monkeypatch.setattr(check, 'history_problems', lambda d: seen.append(d) or [])
    assert check.main(['--data', str(data)]) == 0
    assert seen == [data, data]
    latest = data / 'bundled' / 'latest.json'
    latest.write_text(json.dumps({'patch': '16.22', 'seq': 14, 'approved': 'not_approved'}), encoding='utf-8')
    assert check.main(['--data', str(data)]) == 1
    assert '待审' in capsys.readouterr().err


def test_cli_rejects_a_directory_without_bundled_data(tmp_path, capsys):
    assert check.main(['--data', str(tmp_path)]) == 1
    assert '不是内置数据目录' in capsys.readouterr().err
