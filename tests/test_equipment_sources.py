from copy import deepcopy

from scripts.mix_recommendations import self_collected_recommendations


def test_self_collected_mix_removes_all_legacy_equipment_without_touching_skills():
    skills = [{'max': ['Q', 'E', 'W'], 'win': .51}]
    legacy = {'core': [{'items': ['1', '2', '3']}], 'shoes': [{'item': '1001'}],
              'first': [{'item': '9999'}], 'full': [{'items': ['9999']}],
              'start': [{'items': ['2003']}]}
    base = {'static': {'patch': '16.22'}, 'heroes': {'heroes': {
        '17': {'builds': legacy, 'skills': skills,
               'augments': [{'id': '1029', 'tier': 'A', 'rank': 1}], 'partners': [{'hero': '1'}]},
        '1': {'builds': deepcopy(legacy), 'skills': deepcopy(skills)}}}}
    before = deepcopy(base)
    core = {'17': [{'items': ['3118', '6653', '3087'], 'ordered': True}]}
    shoes = {'17': [{'item': '3006', 'games': 2000, 'wins': 1000}]}
    mixed = self_collected_recommendations(base, core, shoes)
    assert base == before
    assert mixed['heroes']['heroes']['17']['builds'] == {
        'core': core['17'], 'shoes': shoes['17'], 'first': [], 'full': [], 'start': []}
    assert mixed['heroes']['heroes']['1']['builds'] == {
        'core': [], 'shoes': [], 'first': [], 'full': [], 'start': []}
    assert all(h['skills'] == skills for h in mixed['heroes']['heroes'].values())
    assert all(h['augments'] == [] and h['partners'] == [] for h in mixed['heroes']['heroes'].values())
    assert mixed['static'] == base['static']


def test_self_collected_mix_cannot_score_missing_augment_from_tencent_tier(store, monkeypatch):
    from lolhex import recommend

    hero = deepcopy(store.official_hero('17', allow_network=False))
    # 字典中存在、该英雄自采表中缺失的海克斯，仅旧资料给腾讯评级。
    aid = next(a for a in store.augments if a not in store.win_data('17')['augments'])
    hero['augments'] = [{'id': aid, 'tier': 'A', 'rank': 1,
                         'rarity': store.augments[aid]['rarity']}]
    monkeypatch.setattr(store, 'official_hero', lambda *_a, **_kw: hero)
    assert recommend.evaluate(store, '17', [aid, None, None])['slots'][0]['basis'] == 'tier_only'
    mixed = self_collected_recommendations({'heroes': {'heroes': {'17': hero}}}, {}, {})
    monkeypatch.setattr(store, 'official_hero', lambda *_a, **_kw: mixed['heroes']['heroes']['17'])
    row = recommend.evaluate(store, '17', [aid, None, None])['slots'][0]
    assert row['basis'] != 'tier_only'
    assert not row.get('tier')
