from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from studio.catalog import compare, extract as ex, fetch, model, search as se
from studio.llm.scripted import ScriptedLLM
from studio.schemas import (Calc, Candidate, Check, DesignVersion, ManufacturerSpec, ManufacturerValue, Requirement,
                            RobotConcept, SimResult, SourcedValue)

DATASHEET = (Path(__file__).parent / 'fixtures' / 'arm_datasheet.md').read_text()
PUBLIC = '93.184.216.34'


async def public(host, port):
    return [PUBLIC]


def mock(handler):
    return httpx.MockTransport(handler)


def ok_handler(request):
    return httpx.Response(200, content=b'# hello', headers={'content-type': 'text/markdown'})


# --- fetch ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('url', ['http://example.com/a.pdf', 'https://127.0.0.1/a', 'https://10.1.2.3/a',
                                 'https://169.254.169.254/latest', 'https://[::1]/a', 'https://[fc00::1]/a',
                                 'https://[::ffff:10.0.0.1]/a', 'https://0.0.0.0/a'])
async def test_unsafe_urls_rejected(url):
    with pytest.raises(fetch.UnsafeURL):
        await fetch.safe_download(url, resolver=public, transport=mock(ok_handler))


async def test_hostname_resolving_to_private_rejected():
    async def internal(host, port):
        return ['93.184.216.34', '192.168.0.5']
    with pytest.raises(fetch.UnsafeURL):
        await fetch.safe_download('https://evil.example/a', resolver=internal, transport=mock(ok_handler))


async def test_redirect_to_private_rejected():
    async def resolver(host, port):
        return ['10.0.0.8'] if host == 'internal.example' else [PUBLIC]

    def handler(request):
        if request.headers['host'] == 'public.example':
            return httpx.Response(302, headers={'location': 'https://internal.example/secret'})
        return ok_handler(request)
    with pytest.raises(fetch.UnsafeURL):
        await fetch.safe_download('https://public.example/a', resolver=resolver, transport=mock(handler))


async def test_public_https_accepted_and_filename():
    def handler(request):
        return httpx.Response(200, content=b'%PDF-1.4', headers={
            'content-type': 'application/pdf', 'content-disposition': 'attachment; filename="../RA-6 sheet.pdf"'})
    done = await fetch.safe_download('https://acme.example/dl?id=3', resolver=public, transport=mock(handler))
    assert done.bytes == b'%PDF-1.4' and done.filename == 'RA-6 sheet.pdf' and done.content_type == 'application/pdf'
    plain = await fetch.safe_download('https://acme.example/spec', resolver=public, transport=mock(ok_handler))
    assert plain.filename == 'spec.md'


async def test_size_cap_and_content_type():
    big = lambda r: httpx.Response(200, content=b'x' * 5000, headers={'content-type': 'text/plain'})
    with pytest.raises(ValueError, match='size limit'):
        await fetch.safe_download('https://acme.example/a.txt', max_bytes=1000, resolver=public, transport=mock(big))
    exe = lambda r: httpx.Response(200, content=b'MZ', headers={'content-type': 'application/octet-stream'})
    with pytest.raises(ValueError, match='content type'):
        await fetch.safe_download('https://acme.example/a.exe', resolver=public, transport=mock(exe))


# --- search --------------------------------------------------------------------------------------------------------

def requirement(rid, name, target, unit, category):
    return Requirement(id=rid, name=name, category=category, comparator='>=', target=target, unit=unit,
                       calc=Calc(formula='x', inputs={}, result=target, unit=unit))


REQS = [requirement('payload', 'Payload', 3.0, 'kg', 'payload'), requirement('reach_max', 'Reach', 0.8, 'm', 'reach')]
SPEC = SimpleNamespace(title='Pick bottles', summary='Pick and place', environment='lab', payload_description='1 kg bottle')
GENERATED = RobotConcept(family='serial_arm', name='Generated arm', params={})


def sourced(value, unit='', quote='q'):
    return {'value': value, 'unit': unit, 'quote': quote or 'q', 'source_url': 'https://acme.example/ra6'}


def answer():
    return {'candidates': [
        {'manufacturer': 'Acme', 'model': 'RA-6', 'family': 'serial_arm', 'product_url': 'https://acme.example/ra6',
         'payload_kg': sourced('6', 'kg', 'Rated payload 6 kg'), 'reach_m': sourced('900', 'mm', 'Reach 900 mm'),
         'price': {'low': 9000, 'currency': 'USD', 'date': '2026-01-01', 'source_url': 'https://shop.example/ra6',
                   'quote': 'Price $9,000', 'note': 'arm only'}},
        {'manufacturer': 'Acme', 'model': 'RA-6', 'family': 'serial_arm', 'product_url': 'https://acme.example/ra6b',
         'payload_kg': sourced('2', 'kg'), 'reach_m': sourced('1.2 m'),
         'price': {'low': 100, 'source_url': '', 'quote': '', 'date': ''}},
        {'manufacturer': 'Bad', 'model': 'Ftp', 'family': 'serial_arm', 'product_url': 'ftp://bad.example/x'},
        {'manufacturer': 'Mystery', 'model': 'M1', 'family': 'gantry', 'product_url': 'https://m.example/m1',
         'payload_kg': sourced('heavy')},
    ]}


async def test_search_validates_and_screens():
    found = await se.search(ScriptedLLM([('find real robots', answer())]), SPEC, REQS, GENERATED)
    assert [c.id for c in found] == ['acme_ra_6', 'acme_ra_6_2', 'mystery_m1']
    first, second, third = found
    assert first.price and first.price.low == 9000
    assert second.price is None
    assert [(c.requirement_id, c.passed) for c in first.screen] == [('payload', True), ('reach_max', True)]
    assert [(c.requirement_id, c.passed) for c in second.screen] == [('payload', False), ('reach_max', True)]
    assert [c.passed for c in third.screen] == [None, None]


async def test_search_repairs_invalid_json():
    replies = iter(['not json at all', answer()])
    llm = ScriptedLLM([('find real robots', lambda *a: next(replies)), ('repair', lambda *a: answer())])
    found = await se.search(llm, SPEC, REQS, GENERATED, limit=1)
    assert len(found) == 1


def test_screen_units():
    c = Candidate(id='x', manufacturer='A', model='B', family='serial_arm', product_url='https://a.example',
                  payload_kg=SourcedValue(value='13.2', unit='lb', quote='13.2 lb'),
                  reach_m=SourcedValue(value='31.5', unit='in', quote='31.5 in'))
    s = se.screen(c, REQS)
    assert s[0].passed and abs(s[0].actual - 5.987) < 0.01 and s[1].passed is True


# --- extract -------------------------------------------------------------------------------------------------------

def values_answer():
    return {'document_title': 'Acme RA-6', 'kinematics_note': '6 axes', 'values': [
        {'key': 'payload_kg', 'value': 6, 'unit': 'kg', 'quote': 'Rated payload | 6 kg', 'page': '1'},
        {'key': 'reach_m', 'value': 900, 'unit': 'mm', 'quote': 'Maximum reach | 900 mm'},
        {'key': 'mass_kg', 'value': 30, 'unit': 'kg', 'quote': 'Weight | 24 kg'},                   # number not in quote
        {'key': 'repeatability_mm', 'value': 0.01, 'unit': 'mm', 'quote': 'Pose repeatability | ±0.01 mm'},  # invented quote
        {'key': 'j1_max_deg', 'value': 170, 'unit': 'deg', 'quote': 'Axis 1 range ±170°'},
        {'key': 'j1_speed_deg_s', 'value': 180, 'unit': 'deg/s', 'quote': 'maximum speed 180°/s'},
        {'key': 'colour', 'value': 1, 'unit': '', 'quote': 'x'},
    ]}


async def test_extract_verifies_quotes():
    cand = Candidate(id='acme_ra_6', manufacturer='Acme', model='RA-6', family='serial_arm', product_url='https://acme.example')
    spec = await ex.extract(ScriptedLLM([('datasheet', values_answer())]), cand, DATASHEET, 'https://acme.example/ra6.pdf')
    by = {v.key: v for v in spec.values}
    assert 'colour' not in by
    assert by['payload_kg'].verified and by['reach_m'].verified and by['reach_m'].value == pytest.approx(0.9)
    assert by['j1_max_deg'].verified and by['j1_speed_deg_s'].verified
    assert not by['mass_kg'].verified and not by['repeatability_mm'].verified


def test_verify_directly():
    v = ManufacturerValue(key='payload_kg', value=5.44, unit='kg', quote='Payload 12 lb')
    assert ex.verify([v], 'Payload 12 lb')[0].verified
    assert not ex.verify([v], 'Something else')[0].verified


# --- model and compare ---------------------------------------------------------------------------------------------

def mvalue(key, value, unit, verified=True):
    return ManufacturerValue(key=key, value=value, unit=unit, quote='q', verified=verified)


def test_candidate_concept_serial_arm():
    from studio.families.params import SerialArmParams
    generated = RobotConcept(family='serial_arm', name='Gen', params=SerialArmParams().model_dump())
    cand = Candidate(id='acme_ra_6', manufacturer='Acme', model='RA-6', family='serial_arm', product_url='https://a.example')
    mspec = ManufacturerSpec(candidate_id='acme_ra_6', document_url='https://a.example/d.pdf', family='serial_arm', values=[
        mvalue('payload_kg', 6, 'kg'), mvalue('reach_m', 0.9, 'm'), mvalue('dof', 6, ''),
        mvalue('repeatability_mm', 0.03, 'mm'), mvalue('mass_kg', 99, 'kg', verified=False)])
    concept, unverified, _ = model.candidate_concept(mspec, generated, SPEC, cand)
    p = SerialArmParams.model_validate(concept.params)
    assert concept.name == 'Acme RA-6' and p.payload_kg == 6 and p.repeatability_m == pytest.approx(3e-5)
    assert model.reach_of(p) == pytest.approx(0.9)
    assert p.upper_arm_m / p.forearm_m == pytest.approx(0.40 / 0.35)
    assert any('mass_kg' in u for u in unverified) and any('scaled' in u for u in unverified)


def test_differences_flags_low_payload_blocking():
    def check(rid, name, required, actual, unit, passed):
        return Check(requirement_id=rid, name=name, required=required, actual=actual, unit=unit, passed=passed)
    generated = DesignVersion.model_construct(
        spec=SimpleNamespace(physics_inputs=SimpleNamespace(cycle_time_s=10.0)),
        robot=RobotConcept(family='serial_arm', name='Gen', params={'payload_kg': 5.0, 'repeatability_m': 0.0005}),
        sim=SimResult(frames=[], duration_s=6.0, ok=True, checks=[
            check('payload', 'Payload', 1.0, 5.0, 'kg', True), check('reach_max', 'Reach', 0.8, 1.0, 'm', True)]))
    cand_sim = SimResult(frames=[], duration_s=7.0, checks=[
        check('payload', 'Payload', 3.0, 2.0, 'kg', False), check('reach_max', 'Reach', 0.8, 0.82, 'm', True)])
    cand = RobotConcept(family='serial_arm', name='Acme RA-2', params={'payload_kg': 2.0, 'repeatability_m': 0.0005})
    by = {d.topic: d for d in compare.differences(generated, cand_sim, cand)}
    assert by['Payload'].severity == 'blocking'
    assert by['Reach'].severity == 'minor'          # passes, but within 10 % of the requirement
    assert 'Rated payload' not in by                # the Payload requirement row covers it
    assert by['Repeatability'].severity == 'ok'
    assert by['Task duration'].severity == 'minor'


async def test_request_is_pinned_to_validated_ip():
    seen = []

    def handler(request):
        seen.append(request)
        return ok_handler(request)
    await fetch.safe_download('https://acme.example:8443/d/a.md?x=1', resolver=public, transport=mock(handler))
    request = seen[0]
    assert request.url.host == PUBLIC and request.url.port == 8443 and request.url.path == '/d/a.md'
    assert request.headers['host'] == 'acme.example:8443'
    assert request.extensions['sni_hostname'] == 'acme.example'
    v6 = []
    async def resolver6(host, port):
        return ['2606:4700::1111']
    await fetch.safe_download('https://acme.example/a', resolver=resolver6, transport=mock(lambda r: v6.append(r) or ok_handler(r)))
    assert v6[0].url.host == '2606:4700::1111'


async def test_mixed_resolution_never_connects():
    seen = []

    async def mixed(host, port):
        return [PUBLIC, '10.0.0.9']
    with pytest.raises(fetch.UnsafeURL):
        await fetch.safe_download('https://acme.example/a', resolver=mixed, transport=mock(lambda r: seen.append(r) or ok_handler(r)))
    assert seen == []


# --- prices, images and the product-page fallback --------------------------------------------------------------------

def priced(**price):
    base = {'manufacturer': 'Acme', 'model': 'P1', 'family': 'serial_arm', 'product_url': 'https://acme.example/p1'}
    return {'candidates': [{**base, 'price': {'currency': 'USD', 'source_url': 'https://shop.example/p1', 'note': 'arm only', **price}}]}


async def test_price_without_date_is_seen_today():
    llm = ScriptedLLM([('find real robots', priced(low=9000, quote='Price: $9,000.00', date=''))])
    found = await se.search(llm, SPEC, REQS, GENERATED, today='2026-11-20')
    assert found[0].price.date == 'seen 2026-11-20' and found[0].price.low == 9000


async def test_price_not_in_quote_or_without_quote_is_dropped():
    from collections import Counter
    dropped = Counter()
    wrong = await se.search(ScriptedLLM([('find real robots', priced(low=9000, quote='Price: $8,500', date='2026-01-01'))]),
                            SPEC, REQS, GENERATED, dropped=dropped)
    assert wrong[0].price is None and dropped['figure not in the quote'] == 1
    none = await se.search(ScriptedLLM([('find real robots', priced(low=9000, quote='', date='2026-01-01'))]),
                           SPEC, REQS, GENERATED, dropped=dropped)
    assert none[0].price is None and dropped['no verbatim quote'] == 1


def test_price_figures_tolerate_separators():
    assert se.price_problem({'low': 12500, 'source_url': 'u', 'quote': 'EUR 12 500,00 incl. VAT', 'date': 'd'}, 't')[0]
    assert se.price_problem({'high': 1299.5, 'source_url': 'u', 'quote': 'from $1,299.50', 'date': 'd'}, 't')[0]


def html_page(body):
    return lambda url, **kw: _page(url, body)


async def _page(url, body):
    from studio.catalog.fetch import Downloaded
    return Downloaded(body.encode(), 'text/html', url, 'page.html')


def cand(url='https://acme.example/p/x', image=''):
    return Candidate(id='acme_x', manufacturer='Acme', model='X', family='serial_arm', product_url=url, image_url=image)


async def test_fill_images_og_image_and_relative_url():
    page = '<html><head><meta content="/img/x.jpg" property="og:image"></head></html>'
    c, d = cand(), cand(image='https://acme.example/have.jpg')
    await se.fill_images([c, d], html_page(page))
    assert c.image_url == 'https://acme.example/img/x.jpg' and d.image_url == 'https://acme.example/have.jpg'
    t = cand()
    await se.fill_images([t], html_page('<meta name="twitter:image" content="https://cdn.example/t.png">'))
    assert t.image_url == 'https://cdn.example/t.png'


async def test_fill_images_without_og_image_or_on_failure():
    c = cand()
    await se.fill_images([c], html_page('<html><head><title>x</title></head></html>'))
    assert c.image_url == ''

    async def boom(url, **kw):
        raise ValueError('nope')
    await se.fill_images([c], boom)
    assert c.image_url == ''


async def test_analyse_falls_back_to_product_page(tmp_path):
    from studio.pipeline import catalog as pc
    lines = []
    c = cand()
    seen = []

    async def download(url):
        seen.append(url)
        return await _page(url, '<html><body><p>Rated payload 6 kg</p></body></html>')
    doc = await pc.datasheet_text(c, '', lines.append, download)
    assert seen == ['https://acme.example/p/x'] and 'Rated payload 6 kg' in doc.text and doc.source == seen[0]
    assert doc.downloaded and any('product page' in line for line in lines)

    stored = await pc.datasheet_text(c, '', lines.append, download, stored='saved text', stored_source='uploaded document a.pdf')
    assert (stored.text, stored.source, stored.downloaded) == ('saved text', 'uploaded document a.pdf', False)

    async def fail(url):
        raise ValueError('The server answered 404')
    with pytest.raises(pc.DatasheetError, match='Datasheet download failed: The server answered 404'):
        await pc.datasheet_text(c, '', lines.append, fail)


# --- provenance and unpublished values (real FANUC CRX-5iA run, saved fixture) ------------------------------------------

def fanuc():
    import json
    return json.loads((Path(__file__).parent / 'fixtures' / 'real_fanuc_analysis.json').read_text())['analysis']


def fanuc_concept():
    from studio.families.params import SerialArmParams
    a = fanuc()
    mspec = ManufacturerSpec.model_validate(a['manufacturer_spec'])
    generated = RobotConcept(family='serial_arm', name='Gen', params=SerialArmParams().model_dump())
    return a, model.candidate_concept(mspec, generated, SPEC)


def test_fanuc_speed_is_applied_and_defaults_are_marked():
    a, (concept, unverified, prov) = fanuc_concept()
    assert concept.params['max_tcp_speed_m_s'] == 2.0 and prov['max_tcp_speed_m_s'].startswith('verified')
    assert all(prov[k].startswith('default') for k in ('max_torque_nm', 'max_speed_deg_s', 'max_tcp_accel_m_s2'))
    assert prov['payload_kg'].startswith('verified') and prov['upper_arm_m'].startswith('derived')
    assert prov['gripper_force_n'].startswith('carried')


def test_unpublished_checks_get_no_verdict_but_keep_their_estimate():
    a, (concept, unverified, prov) = fanuc_concept()
    checks = {c.requirement_id: c for c in model.qualify_checks([Check.model_validate(c) for c in a['sim']['checks']], prov)}
    for rid in ('joint_torque_j1', 'joint_torque_j6', 'joint_speed', 'tcp_accel', 'cycle_time'):
        assert checks[rid].passed is None and checks[rid].detail.startswith('Not published'), rid
    assert checks['joint_torque_j2'].actual == 211.0 and 'max_torque_nm assumed 211' in checks['joint_torque_j2'].detail
    assert checks['tcp_speed'].passed is True and checks['payload'].passed is True
    assert checks['grip_force'].passed is True and "generated design's gripper" in checks['grip_force'].detail
    assert checks['collision_free'].passed is True and 'estimated from the verified reach' in checks['collision_free'].detail
    assert checks['reach_max'].passed is True and checks['joint_limits'].passed is True
    ok = [c for c in checks.values() if c.passed is not None]
    assert all(c.passed for c in ok)


def test_differences_render_verdicts_and_skip_info_and_duplicates():
    a, (concept, unverified, prov) = fanuc_concept()
    checks = model.qualify_checks([Check.model_validate(c) for c in a['sim']['checks']], prov)
    sim = SimResult(frames=[], duration_s=6.0, checks=checks)
    info = requirement('reach_min', 'Closest task point', 0.8, 'm', 'reach').model_copy(update={'comparator': 'info'})
    reqs = [info, requirement('payload', 'Payload', 1.0, 'kg', 'payload')]
    generated = DesignVersion.model_construct(
        spec=SimpleNamespace(physics_inputs=SimpleNamespace(cycle_time_s=6.0)), requirements=reqs,
        robot=RobotConcept(family='serial_arm', name='Gen', params={'payload_kg': 1.0, 'repeatability_m': 0.0005}),
        sim=SimResult(frames=[], duration_s=6.0, ok=True, checks=checks))
    rows = compare.differences(generated, sim, concept, prov)
    by = {d.topic: d for d in rows}
    assert [d.topic for d in rows].count('Repeatability') == 1      # the accuracy row; no duplicate rating row
    assert by['No collisions with the scene'].candidate.startswith('passes')
    assert by['No collisions with the scene'].generated.startswith('passes')
    assert by['Static load j1'].candidate.startswith('not published: Not published') or 'not published' in by['Static load j1'].candidate
    assert 'Closest task point' not in by
    assert 'Rated payload' not in by
