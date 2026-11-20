"""Motor sourcing (robot/sourcing.py) with a scripted search role and saved shop pages: no network, no real model."""
import re
from pathlib import Path

import pytest

from studio.catalog.fetch import Downloaded
from studio.llm.registry import LLMRegistry
from studio.llm.scripted import ScriptedLLM
from studio.robot import sizing as sz
from studio.robot import sourcing as so
from studio.schemas import JointSizing, MotorAlternative, ShopPrice, SpecValue

SHOPS = Path(__file__).parent / 'fixtures' / 'shops'
ROBU = 'https://robu.in/product/nema23-3nm'
SOL = 'https://www.omc-stepperonline.com/nema23-1-26nm'
PAGES = {ROBU: (SHOPS / 'robu_nema23_3nm.html').read_bytes(), SOL: (SHOPS / 'stepperonline_23hs_1_26nm.html').read_bytes()}
DATE = '2026-11-20'


async def fake_download(url, max_bytes=0, timeout_s=0):
    if url not in PAGES:
        raise ValueError('404')
    return Downloaded(PAGES[url], 'text/html', url, 'page.html')


def v(key, value, unit, quote):
    return {'key': key, 'value': value, 'unit': unit, 'quote': quote}


GOOD = {'name': 'NEMA 23 Stepper Motor 3 N.m 76mm', 'url': ROBU, 'motor_type': 'stepper',
        'values': [v('holding_torque_nm', 3.0, 'N.m', 'Holding Torque: 3.0 N.m'),
                   v('current_a', 4.2, 'A', 'Rated Current: 4.2 A'), v('voltage_v', 24, 'V', 'Rated Voltage: 24 V'),
                   v('rated_speed_rpm', 600, 'rpm', 'Rated Speed: 600 rpm'),
                   v('mass_kg', 9.9, 'kg', 'Holding Torque: 9.9 N.m')],       # the quote is in a script, not the page text
        'price': {'amount': 2499, 'currency': 'INR', 'quote': 'Rs. 2,499.00 (inclusive of GST)', 'source_url': ROBU}}
BAD = {'name': 'Nema 23 Stepper Motor 1.26Nm', 'url': SOL, 'motor_type': 'stepper',
       'values': [v('holding_torque_nm', 4.5, 'N.m', 'Holding Torque 4.5 N.m'),            # NOT on the page
                  v('current_a', 2.8, 'A', 'Rated Current/phase 2.8 A'), v('voltage_v', 24, 'V', 'Rated Voltage 24 V')],
       'price': {'amount': 18.5, 'currency': 'USD', 'quote': 'Price: US $18.50', 'source_url': SOL}}
OFF = {'name': 'Mystery motor', 'url': 'https://example.com/motor', 'values': []}
INSECURE = {'name': 'Plain http motor', 'url': 'http://robu.in/product/x', 'values': []}


def registry(tmp_path, candidates):
    return LLMRegistry(tmp_path, {'search': ScriptedLLM([('find real motors', {'candidates': candidates})])})


def need(joint='j2'):
    return {'joint': joint, 'kind': 'stepper', 'family': 'serial_arm', 'hint': 'shoulder',
            'motor_side': {'continuous_nm': 1.0, 'peak_nm': 1.2, 'speed_rpm': 100.0}}


def joint(**over):
    base = dict(joint='j2', kind='revolute', static_torque=15.0, accel_torque=3.0, continuous=20.0, peak=25.0, speed=1.0,
                calcs=[sz._calc('J_load = test', {'x': 1}, 0.4, 'kg*m^2')])
    base.update(over)
    return JointSizing(**base)


async def found(tmp_path, candidates=(GOOD, BAD, OFF, INSECURE)):
    out = await so.search_motors(registry(tmp_path, list(candidates)), [need()], DATE, lambda t: None, download=fake_download)
    return {a.id: a for a in out['j2']}


async def test_quote_not_in_the_page_drops_the_value(tmp_path):
    alts = await found(tmp_path)
    assert set(alts) == {'robu_nema_23_stepper_motor_3_n_m_76mm', 'stepperonline_nema_23_stepper_motor_1_26nm'}
    good, bad = alts['robu_nema_23_stepper_motor_3_n_m_76mm'], alts['stepperonline_nema_23_stepper_motor_1_26nm']
    gv = {x.key: x for x in good.values}
    assert gv['holding_torque_nm'].status == 'verified' and gv['holding_torque_nm'].value == 3.0
    assert gv['holding_torque_nm'].quote == 'Holding Torque: 3.0 N.m' and gv['rated_speed_rpm'].value == 600
    assert 'mass_kg' in gv and gv['mass_kg'].status == 'assumed'      # the 9.9 quote (a script tag) was dropped; table mass
    assert gv['mass_kg'].value == 1.65                                # NEMA 23 stack nearest to 3 N*m
    bv = {x.key: x for x in bad.values}
    assert 'holding_torque_nm' not in bv and bv['current_a'].status == 'verified'


async def test_frame_dimensions_are_assumed_from_the_table(tmp_path):
    good = (await found(tmp_path))['robu_nema_23_stepper_motor_3_n_m_76mm']
    gv = {x.key: x for x in good.values}
    assert gv['frame'].text == 'NEMA 23' and gv['frame'].status == 'assumed'   # named in the product, not quoted
    for key, value in (('flange_mm', 56.4), ('bolt_circle_mm', 66.7), ('pilot_mm', 38.1), ('shaft_d_mm', 8.0)):
        assert gv[key].value == value and gv[key].status == 'assumed'
    assert so.NEMA_TABLE['23']['flange_mm'] == 56.4


async def test_quoted_frame_is_verified(tmp_path):
    cand = {**GOOD, 'values': [v('frame', 'NEMA 23', '', 'Frame size: NEMA 23 (57 mm)'), v('frame', 'NEMA 34', '', 'Frame size: NEMA 34')]}
    gv = {x.key: x for x in (await found(tmp_path, [cand]))['robu_nema_23_stepper_motor_3_n_m_76mm'].values}
    assert gv['frame'].status == 'verified' and gv['frame'].text == 'NEMA 23' and gv['flange_mm'].status == 'assumed'


async def test_price_is_verified_and_dated(tmp_path):
    alts = await found(tmp_path)
    p = alts['robu_nema_23_stepper_motor_3_n_m_76mm'].price
    assert isinstance(p, ShopPrice) and p.amount == 2499 and p.currency == 'INR' and p.seen == DATE and p.shop == 'robu'
    assert p.source_url == ROBU and 'inclusive of GST' in p.quote
    wrong = {**GOOD, 'price': {**GOOD['price'], 'amount': 1999}}                 # figure not in the quote
    nopage = {**GOOD, 'price': {**GOOD['price'], 'quote': 'Rs. 2,499.00 only today'}}   # quote not on the page
    for cand in (wrong, nopage, {**GOOD, 'price': {'amount': 5, 'quote': '', 'source_url': ROBU}}):
        assert (await found(tmp_path, [cand]))['robu_nema_23_stepper_motor_3_n_m_76mm'].price is None


async def test_off_allowlist_and_insecure_urls_are_dropped(tmp_path):
    alts = await found(tmp_path)
    assert all(a.url.startswith('https://') and so.shop_of(a.url) for a in alts.values())
    assert so.shop_of('https://example.com/x') is None and so.shop_of('http://robu.in/x') is None
    assert so.shop_of('https://www.robu.in/x') == 'robu' and so.shop_of('https://robu.in.evil.com/x') is None
    moved = {**GOOD, 'values': [{**GOOD['values'][0], 'source_url': 'https://evil.example/page'}]}
    got = (await found(tmp_path, [moved]))['robu_nema_23_stepper_motor_3_n_m_76mm']
    assert 'holding_torque_nm' not in {x.key for x in got.values}


async def test_unit_conversion_and_ids(tmp_path):
    assert so.to_si('holding_torque_nm', 425, 'oz.in') == pytest.approx(425 * 0.00706155)
    assert so.to_si('rotor_inertia_kgm2', 480, 'g.cm²') == pytest.approx(480e-7)
    assert so.to_si('current_a', 2800, 'mA') == pytest.approx(2.8) and so.to_si('rated_speed_rpm', 5, 'rps') == 300 and so.to_si('x', 1, 'm') is None
    twin = await found(tmp_path, [GOOD, {**GOOD, 'values': GOOD['values'][:1]}])
    assert len(twin) == 2 and all(re.fullmatch(r'^[a-z][a-z0-9_]{0,62}$', k) for k in twin)
    assert {a.shop for a in twin.values()} == {'robu'}


async def test_search_groups_joints_and_runs_once_per_class(tmp_path):
    llm = ScriptedLLM([('find real motors', {'candidates': [GOOD]})])
    reg = LLMRegistry(tmp_path, {'search': llm})
    big = {**need('j1'), 'motor_side': {'continuous_nm': 40.0, 'peak_nm': 60.0, 'speed_rpm': 100.0}}
    lines = []
    out = await so.search_motors(reg, [need('j2'), need('j3'), big], DATE, lines.append, download=fake_download)
    assert len(llm.calls) == 2 and set(out) == {'j1', 'j2', 'j3'} and len(lines) == 2
    assert out['j2'][0].id == out['j3'][0].id and out['j2'][0] is not out['j3'][0]


async def test_select_picks_the_passing_alternative_with_a_drive(tmp_path):
    alts = await found(tmp_path)
    choices = so.select([joint()], {'j2': list(alts.values())}, 'serial_arm', {})
    c = choices[0]
    assert c.source == 'shop' and c.alternative_id == 'robu_nema_23_stepper_motor_3_n_m_76mm' and not c.pinned
    assert c.drive.type == 'planetary' and c.drive.ratio == 20     # lowest ratio that carries 20 / 25 N*m
    assert c.margins['continuous'] == pytest.approx(3.0 * 0.7 * 20 * 0.85 / 20) and min(c.margins.values()) >= 1
    assert c.required['continuous'] == 20.0 and c.provided['continuous'] == pytest.approx(35.7)
    assert all(ch.passed is not False for ch in c.checks) and c.price.amount == 2499
    by = {a.id: a for a in c.alternatives}
    assert by[c.alternative_id].passes and not by['stepperonline_nema_23_stepper_motor_1_26nm'].passes
    assert 'torque' in by['stepperonline_nema_23_stepper_motor_1_26nm'].reason


async def test_pinned_alternative_is_honoured_even_when_it_fails(tmp_path):
    alts = await found(tmp_path)
    bad = 'stepperonline_nema_23_stepper_motor_1_26nm'
    c = so.select([joint()], {'j2': list(alts.values())}, 'serial_arm', {'j2': bad})[0]
    assert c.alternative_id == bad and c.pinned and c.source == 'shop'
    failing = [ch for ch in c.checks if ch.passed is False]
    assert failing and 'torque' in failing[0].detail
    # a pin on an unknown id is ignored: the best passing alternative is used
    c2 = so.select([joint()], {'j2': list(alts.values())}, 'serial_arm', {'j2': 'nope'})[0]
    assert c2.alternative_id.startswith('robu_') and not c2.pinned


async def test_pinned_motor_that_fails_on_torque_shows_the_margin():
    weak = MotorAlternative(id='robu_weak', name='Weak', motor_type='stepper', values=[
        SpecValue(key=k, value=x, status='verified', quote='q') for k, x in
        (('holding_torque_nm', 0.4), ('rated_speed_rpm', 600.0), ('current_a', 1.5), ('voltage_v', 24.0))])
    c = so.select([joint()], {'j2': [weak]}, 'serial_arm', {'j2': 'robu_weak'})[0]
    assert c.pinned and min(c.margins.values()) < 1 and any(ch.passed is False for ch in c.checks)


async def test_nothing_passes_gives_a_defined_motor(tmp_path):
    alts = await found(tmp_path, [BAD])
    c = so.select([joint()], {'j2': list(alts.values())}, 'serial_arm', {})[0]
    assert c.source == 'defined' and c.alternative_id is None and c.name.startswith('Defined spec')
    assert len(c.alternatives) == 1 and not c.alternatives[0].passes and c.price is None
    assert so.select([joint()], {}, 'serial_arm', {})[0].source == 'defined'


def priced(aid, amount, currency):
    price = None if amount is None else ShopPrice(amount=amount, currency=currency, seen=DATE, shop='robu',
                                                  source_url='https://robu.in/x')
    return MotorAlternative(id=aid, name=aid, motor_type='stepper', price=price, values=[
        SpecValue(key=k, value=x, status='verified', quote='q') for k, x in
        (('holding_torque_nm', 3.0), ('rated_speed_rpm', 600.0), ('current_a', 2.8), ('voltage_v', 24.0))])


def test_cheapest_ranks_inr_first_then_currencies_then_unpriced():
    alts = [priced('robu_none', None, ''), priced('evelta_usd_1', 1.0, 'USD'), priced('robu_inr_3000', 3000, 'INR'),
            priced('robu_inr_2500', 2500, 'INR'), priced('robu_eur_9', 9.0, 'EUR')]
    assert [a.id for a in sorted(alts, key=so.price_rank)] == ['robu_inr_2500', 'robu_inr_3000', 'robu_eur_9',
                                                                'evelta_usd_1', 'robu_none']
    c = so.select([joint()], {'j2': alts}, 'serial_arm', {})[0]
    assert c.alternative_id == 'robu_inr_2500'                    # a USD 1 listing does not beat the cheapest INR one
    only_usd = so.select([joint()], {'j2': [alts[1], alts[0]]}, 'serial_arm', {})[0]
    assert only_usd.alternative_id == 'evelta_usd_1'


def test_nema_table_documented_data():
    assert set(so.NEMA_TABLE) == {'17', '23', '34'}
    for frame in so.NEMA_TABLE.values():
        assert {'flange_mm', 'bolt_circle_mm', 'pilot_mm', 'shaft_d_mm', 'stacks'} <= set(frame)
        torques = [s['holding_torque_nm'] for s in frame['stacks']]
        assert torques == sorted(torques)
    assert set(so.SHOPS.values()) >= {'robu', 'robokits', 'evelta', 'amazon_in', 'stepperonline', 'pololu', 'servocity', 'aliexpress'}


def test_select_stepper_without_speed_uses_assumed_speed_on_the_choice():
    vals = [SpecValue(key=k, value=x, status='verified', quote=k) for k, x in
            (('holding_torque_nm', 3.0), ('current_a', 2.8), ('voltage_v', 24.0))]
    vals.append(SpecValue(key='frame', text='NEMA 23', status='verified', quote='NEMA 23'))
    alt = MotorAlternative(id='pololu_x', name='NEMA 23 stepper', motor_type='stepper', values=vals)
    c = so.select([joint()], {'j2': [alt]}, 'serial_arm', {})[0]
    assert c.source == 'shop' and c.alternative_id == 'pololu_x'
    speed = next(x for x in c.values if x.key == 'rated_speed_rpm')
    assert speed.status == 'assumed' and speed.value == 300.0
    by = {a.id: a for a in c.alternatives}
    assert by['pololu_x'].passes and 'speed assumed (conservative)' in by['pololu_x'].reason
