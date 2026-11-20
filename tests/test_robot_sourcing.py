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
    assert c.drive.type == 'planetary' and c.drive.ratio == 15     # smallest standard ratio (3,4,5,7,10,15,..) that carries 20 / 25 N*m
    assert c.margins['continuous'] == pytest.approx(3.0 * 0.7 * 15 * 0.85 / 20) and min(c.margins.values()) >= 1
    assert c.required['continuous'] == 20.0 and c.provided['continuous'] == pytest.approx(3.0 * 0.7 * 15 * 0.85)
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


# --- realism round: unverified prices, rejection reasons, ratio choice, preferred vendors, electronics -------------------

from studio.catalog.vendors import normalize as normalize_vendors  # noqa: E402
from studio.schemas import ElectronicsItem  # noqa: E402


async def blocked_download(url, max_bytes=0, timeout_s=0):
    raise ValueError('The server answered 403')


def challenge_download(url, max_bytes=0, timeout_s=0):
    async def go():
        return Downloaded(b'<html><title>Just a moment...</title><body>Checking your browser before accessing</body></html>',
                          'text/html', url, 'page.html')
    return go()


async def test_blocked_fetch_keeps_the_price_unverified_and_unranked(tmp_path):
    notes = []
    out = await so.search_motors(registry(tmp_path, [GOOD]), [need()], DATE, lambda t: None, download=blocked_download, notes=notes)
    alt = out['j2'][0]
    assert alt.price.status == 'unverified' and alt.price.amount == 2499 and alt.price_issue == 'page_blocked'
    assert 'holding_torque_nm' not in {v.key for v in alt.values}        # values need the page: never unverified
    assert any('robu.in' in n and 'HTTP 403' in n for n in notes)
    assert so.price_rank(alt)[1] == 2                                  # counts as unpriced
    assert alt.price.model_dump()['status'] == 'unverified'
    assert 'status' not in ShopPrice(amount=1, seen=DATE, shop='robu', source_url='https://robu.in/x').model_dump()


async def test_challenge_page_counts_as_blocked_but_a_wrong_quote_does_not(tmp_path):
    out = await so.search_motors(registry(tmp_path, [GOOD]), [need()], DATE, lambda t: None, download=challenge_download)
    assert out['j2'][0].price.status == 'unverified'
    nopage = {**GOOD, 'price': {**GOOD['price'], 'quote': 'Rs. 2,499.00 only today'}}
    alt = (await found(tmp_path, [nopage]))['robu_nema_23_stepper_motor_3_n_m_76mm']
    assert alt.price is None and alt.price_issue == 'quote_not_found'
    wrong = {**GOOD, 'price': {**GOOD['price'], 'amount': 1999}}
    assert (await found(tmp_path, [wrong]))['robu_nema_23_stepper_motor_3_n_m_76mm'].price_issue == 'price_not_in_quote'
    none = {**GOOD, 'price': None}
    assert (await found(tmp_path, [none]))['robu_nema_23_stepper_motor_3_n_m_76mm'].price_issue == 'no_price'


async def test_rejected_entries_carry_reasons_and_notes(tmp_path):
    alts = await found(tmp_path)
    c = so.select([joint()], {'j2': list(alts.values())}, 'serial_arm', {})[0]
    assert [r.id for r in c.rejected] == ['stepperonline_nema_23_stepper_motor_1_26nm']
    assert c.rejected[0].reason.startswith('missing_values: ') and 'torque' in c.rejected[0].reason   # its quote was not on the page
    weak = MotorAlternative(id='robu_weak', name='Weak', shop='robu', motor_type='stepper', values=[
        SpecValue(key=k, value=x, status='verified', quote='q') for k, x in
        (('holding_torque_nm', 0.4), ('rated_speed_rpm', 600.0), ('current_a', 1.5), ('voltage_v', 24.0))])
    c3 = so.select([joint()], {'j2': [weak] + list(alts.values())[:1]}, 'serial_arm', {})[0]
    assert c3.rejected[0].id == 'robu_weak' and c3.rejected[0].reason.startswith('fails_margin: ')
    assert 'torque binds' in c3.rejected[0].reason or 'torque needs ratio' in c3.rejected[0].reason
    nov = MotorAlternative(id='robu_nov', name='No values', shop='robu', motor_type='stepper')
    c2 = so.select([joint()], {'j2': [nov]}, 'serial_arm', {})[0]
    assert c2.source == 'defined' and c2.rejected[0].reason.startswith('missing_values: ')
    notes = so.sourcing_notes([c, c2], [], ['search line'])
    assert notes[0] == 'search line' and 'no shop product passed' in notes[2] and 'missing_values' in notes[2]
    assert c.calcs and c.calcs[1].formula.startswith('tau_m_peak')


def motor_values(**over):
    base = {'holding_torque_nm': 3.0, 'rated_speed_rpm': 600.0, 'current_a': 2.8, 'voltage_v': 24.0}
    base.update(over)
    return [SpecValue(key=k, value=x, status='verified', quote=f'{k} {x}') for k, x in base.items()]


def alt(values, kind='stepper'):
    return MotorAlternative(id='robu_x', name='X', motor_type=kind, values=values)


def test_ratio_choice_names_the_binding_constraint():
    s = joint(peak=112.0, continuous=20.0, speed=2.09)                 # the bottle arm's j1 numbers
    weak = alt(motor_values(holding_torque_nm=0.8, rated_speed_rpm=300.0))
    d, ok, _, _, reason = sz.choose_drive(weak, s, sz.drive_options('serial_arm', 0, s), 'stepper')
    assert not ok and 'torque binds' in reason and '112' in reason
    mid = alt(motor_values(holding_torque_nm=3.0, rated_speed_rpm=300.0))
    reason = sz.choose_drive(mid, s, sz.drive_options('serial_arm', 0, s), 'stepper')[4]
    assert 'torque needs ratio >= 100:1 but speed allows <= 10:1' in reason
    slow = alt(motor_values(holding_torque_nm=3.0, rated_speed_rpm=20.0))
    assert 'speed binds' in sz.choose_drive(slow, joint(speed=5.0), sz.drive_options('serial_arm', 1, joint()), 'stepper')[4]
    # smallest ratio that fits both: 3 N*m holding (2.1 N*m rated) at 600 rpm for 25 N*m peak, 20 continuous
    good = alt(motor_values())
    d, ok, margins, _, _ = sz.choose_drive(good, joint(), sz.drive_options('serial_arm', 1, joint()), 'stepper')
    assert ok and d.ratio == 15 and margins['speed'] >= 1
    smaller = [x for x in sz.drive_options('serial_arm', 1, joint()) if x.ratio < d.ratio]
    assert all(not sz.choose_drive(good, joint(), [x], 'stepper')[1] for x in smaller)


def test_published_speed_ranks_before_price_and_preferred_before_both():
    cheap = priced('robu_assumed', 1000, 'INR')
    cheap.values = [v_ for v_ in cheap.values if v_.key != 'rated_speed_rpm'] + [SpecValue(key='frame', text='NEMA 23', status='verified', quote='q')]
    dear = priced('robu_published', 3000, 'INR')
    c = so.select([joint()], {'j2': [cheap, dear]}, 'serial_arm', {})[0]
    assert c.alternative_id == 'robu_published'
    pref = priced('evelta_pref', 9000, 'INR').model_copy(update={'preferred': True})
    assert so.select([joint()], {'j2': [cheap, dear, pref]}, 'serial_arm', {})[0].alternative_id == 'evelta_pref'
    assert so.select([joint()], {'j2': [cheap, dear, pref]}, 'serial_arm', {})[0].preferred is True
    unver = priced('robu_unver', 1, 'INR')
    unver.price = unver.price.model_copy(update={'status': 'unverified'})
    assert so.price_rank(unver)[1] == 2 and so.price_rank(dear)[1] == 0


async def test_preferred_vendors_extend_the_allowlist_and_flag_products(tmp_path):
    vendors = normalize_vendors([{'name': 'Robu', 'domain': 'robu.in', 'categories': ['motors']},
                                 {'name': 'Acme', 'domain': 'acme-motors.com', 'categories': ['motors']}])
    llm = ScriptedLLM([('find real motors', {'candidates': [GOOD, {'name': 'Acme M', 'url': 'https://acme-motors.com/m1', 'values': []}]})])
    out = await so.search_motors(LLMRegistry(tmp_path, {'search': llm}), [need()], DATE, lambda t: None,
                                 download=fake_download, vendors=vendors)
    by = {a.shop: a for a in out['j2']}
    assert by['robu'].preferred and by['acme_motors_com'].preferred and 'Search these preferred vendors first' in llm.calls[0]['system_prompt']
    assert 'acme-motors.com' in llm.calls[0]['system_prompt']
    none = await so.search_motors(LLMRegistry(tmp_path, {'search': ScriptedLLM([('find real motors', {'candidates': [GOOD, {'name': 'Acme M', 'url': 'https://acme-motors.com/m1', 'values': []}]})])}),
                                  [need()], DATE, lambda t: None, download=fake_download)
    assert [a.shop for a in none['j2']] == ['robu'] and not none['j2'][0].preferred


def test_electronics_selection_replaces_defined_items_and_lists_rejections():
    motor = so.select([joint()], {}, 'serial_arm', {})[0]
    defined = sz.electronics_for([motor.model_copy(update={'values': motor.values})], 'serial_arm')
    needs = so.electronics_needs(defined)
    assert {n['role'] for n in needs} == {'driver', 'power_supply'}
    drv = next(i for i in defined if i.role == 'driver')
    need_a = drv.checks[0].required

    def cand(cid, amps, volts, price=None, role='driver', watts=None, kind='stepper'):
        vals = [SpecValue(key='current_a', value=amps, status='verified', quote='q'),
                SpecValue(key='voltage_v', value=volts, status='verified', quote='q')]
        if kind:
            vals.append(SpecValue(key='driver_type', text=kind, status='verified', quote='q'))
        if watts:
            vals.append(SpecValue(key='power_w', value=watts, status='verified', quote='q'))
        return {'id': cid, 'name': cid, 'shop': 'robu', 'url': 'https://robu.in/' + cid, 'role': role, 'values': vals,
                'price': price, 'price_issue': '' if price else 'no_price', 'preferred': False}
    inr = ShopPrice(amount=900, currency='INR', seen=DATE, shop='robu', source_url='https://robu.in/x')
    cheap = ShopPrice(amount=500, currency='INR', seen=DATE, shop='robu', source_url='https://robu.in/y')
    cands = {drv.id: [cand('weak', need_a / 2, 48), cand('good', need_a * 2, 48, inr), cand('lowv', need_a * 2, 5),
                      cand('brushed', need_a * 2, 48, cheap, kind='dc'), cand('untyped', need_a * 2, 48, cheap, kind=None)]}
    out = so.select_electronics(defined, cands)
    new = next(i for i in out if i.id == drv.id)
    assert new.source == 'shop' and new.name == 'good' and new.price.amount == 900 and new.checks[0].passed
    assert {r.id: r.reason.split(':')[0] for r in new.rejected} == {'weak': 'fails_margin', 'lowv': 'fails_margin',
                                                                     'brushed': 'fails_margin', 'untyped': 'missing_values'}
    why = {r.id: r.reason for r in new.rejected}
    assert 'driver type dc cannot drive stepper motors' in why['brushed'] and 'verified driver type' in why['untyped']
    assert next(n for n in needs if n['role'] == 'driver')['driver_type'] == 'stepper'
    assert next(i for i in out if i.role == 'power_supply').source == 'defined'


async def test_driver_type_counts_only_when_the_quote_names_it():
    """A brushed DC driver was chosen for stepper joints in real build b2 (2026-11-21): the type now needs a quote."""
    page = 'Pololu G2 High-Power Motor Driver 24v21. This discrete MOSFET H-bridge drives a single brushed DC motor.'

    class OnePage:
        async def get(self, url):
            return page, 'ok'
    shops = so.shop_map()
    url = 'https://www.pololu.com/product/2995'
    claimed = [{'key': 'driver_type', 'value': 'stepper', 'quote': 'drives a single brushed DC motor'}]
    assert await so._verified_values(claimed, url, OnePage(), shops) == []
    honest = [{'key': 'driver_type', 'value': 'dc', 'quote': 'drives a single brushed DC motor'}]
    got = await so._verified_values(honest, url, OnePage(), shops)
    assert [(v.key, v.text, v.status) for v in got] == [('driver_type', 'dc', 'verified')]
