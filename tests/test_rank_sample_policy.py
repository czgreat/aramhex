import pytest
from lolhex import rank


@pytest.mark.parametrize('games',[20,21,30,31])
def test_personal_score_and_sample_boundaries(games):
    r={'region_name':'艾欧尼亚','games':games,'score_eligible':games>20,
       'score':2400.5 if games>20 else None,'top':.03 if games>20 else None,
       'band':'high' if games>20 else None,'band_name':'高分' if games>20 else None,
       'solo_top':.05,'solo_games':18}
    text=rank.describe({'as_of':'2026-11-08','results':[r]})
    assert ('2400.5 分' in text)==(games>20)
    assert ('样本较少' in text)==(games<=30)
    assert '单排对局水平' in text and '单排 18 局' not in text and '单排分' not in text
    if games<=20:assert '前 3' not in text and '高分' not in text


def test_multiple_regions_and_old_cached_payload():
    old={'region_name':'艾欧尼亚','games':100,'top':.03,'band_name':'高分'}
    small={'region_name':'联盟二区','games':10,'score':None,'top':None,'band_name':None,'score_eligible':False}
    text=rank.describe({'results':[old,small]})
    assert '艾欧尼亚' in text and '联盟二区' in text and '样本较少' in text
