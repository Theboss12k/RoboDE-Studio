"""Preferred vendors: normalisation, URL matching and the prompt sentence (pure functions)."""
from studio.catalog import vendors as vd

RAW = [
    {'name': 'Robu', 'domain': 'https://www.Robu.in/shop', 'categories': ['motors', 'electronics', 'bogus'], 'note': 'India'},
    {'name': 'Robu again', 'domain': 'robu.in', 'categories': ['robots'], 'note': 'x'},
    {'name': '', 'domain': 'Pololu.com:443', 'categories': ['motors']},
    {'name': 'bad', 'domain': 'localhost', 'categories': ['motors']},
    {'name': 'nocat', 'domain': 'a.com', 'categories': []},
    'junk', {'domain': '1.2.3.4', 'categories': ['robots']},
]


def test_normalize_cleans_merges_and_skips():
    got = vd.normalize(RAW)
    assert [(v.domain, v.categories) for v in got] == [('robu.in', ('robots', 'motors', 'electronics')), ('pololu.com', ('motors',))]
    assert got[0].name == 'Robu' and got[0].note == 'India' and got[1].name == 'pololu.com'
    assert vd.normalize(None) == [] and vd.normalize('x') == []


def test_normalize_caps_at_fifty():
    many = [{'name': f'v{i}', 'domain': f'shop{i}.com', 'categories': ['robots']} for i in range(60)]
    assert len(vd.normalize(many)) == 50


def test_matches_host_or_subdomain_by_category():
    v = vd.normalize(RAW)
    assert vd.matches('https://robu.in/p/1', v, 'motors').name == 'Robu'
    assert vd.matches('https://m.shop.robu.in/p', v, 'robots').domain == 'robu.in'
    assert vd.matches('https://www.pololu.com/x', v, 'motors').domain == 'pololu.com'
    assert vd.matches('https://pololu.com/x', v, 'robots') is None          # not a robots vendor
    assert vd.matches('https://notrobu.in/x', v, 'motors') is None          # a suffix is not a sub-domain
    assert vd.matches('https://evil.com/robu.in', v, 'motors') is None
    assert vd.matches('', v, 'motors') is None


def test_matches_accepts_raw_dicts():
    assert vd.matches('https://robu.in/a', RAW, 'electronics') is not None


def test_prompt_block():
    v = vd.normalize(RAW)
    text = vd.prompt_block(v, 'motors')
    assert text.startswith('Search these preferred vendors first: ') and 'Robu (robu.in): India' in text and 'pololu.com (pololu.com)' in text
    assert 'pololu' not in vd.prompt_block(v, 'robots')
    assert vd.prompt_block(v, 'electronics').count('robu.in') == 1
    assert vd.prompt_block([], 'robots') == '' and vd.prompt_block(None, 'motors') == ''


# --- catalog ranking (scripted search) ---------------------------------------------------------------------------

async def test_search_marks_and_ranks_preferred_first_and_prompts_for_them():
    from test_catalog import GENERATED, REQS, SPEC, answer
    from studio.catalog import search as se
    from studio.llm.scripted import ScriptedLLM
    seen = []

    def reply(prompt, system='', images=None):
        seen.append(prompt)
        data = answer()
        data['candidates'].append({'manufacturer': 'Robu', 'model': 'Arm1', 'family': 'serial_arm',
                                   'product_url': 'https://www.robu.in/product/arm1'})
        return data
    vend = vd.normalize([{'name': 'Robu', 'domain': 'robu.in', 'categories': ['robots'], 'note': 'India'}])
    found = await se.search(ScriptedLLM([('find real robots', reply)]), SPEC, REQS, GENERATED, vendors=vend)
    assert [c.id for c in found] == ['robu_arm1', 'acme_ra_6', 'acme_ra_6_2', 'mystery_m1']
    assert [c.preferred for c in found] == [True, False, False, False]
    assert 'Search these preferred vendors first: Robu (robu.in): India' in seen[0]
    dumped = [c.model_dump(mode='json') for c in found]
    assert dumped[0]['preferred'] is True and all('preferred' not in d for d in dumped[1:])
    # a limit does not cut the preferred one
    only = await se.search(ScriptedLLM([('find real robots', reply)]), SPEC, REQS, GENERATED, vendors=vend, limit=1)
    assert [c.id for c in only] == ['robu_arm1']


async def test_search_without_vendors_is_unchanged():
    from test_catalog import GENERATED, REQS, SPEC, answer
    from studio.catalog import search as se
    from studio.llm.scripted import ScriptedLLM
    seen = []
    found = await se.search(ScriptedLLM([('find real robots', lambda p, s='', i=None: seen.append(p) or answer())]), SPEC, REQS, GENERATED)
    assert 'preferred' not in seen[0] and all('preferred' not in c.model_dump(mode='json') for c in found)
