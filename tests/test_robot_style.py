"""Structure style (robot/style.py): the agent proposes, code checks and may force machined aluminium."""

import pytest
from test_pipeline import make_v1, setup

from studio.llm.registry import LLMRegistry
from studio.llm.scripted import ScriptedLLM
from studio.physics.statics import G
from studio.robot import style as st
from studio.robot import sizing as sz
from studio.schemas import RobotConcept


@pytest.fixture
async def v1(tmp_path):
    return await make_v1(setup(tmp_path))


def agent(tmp_path, style, reasons=('light payload',)):
    llm = ScriptedLLM([('structure style', {'style': style, 'reasons': list(reasons)})])
    return LLMRegistry(tmp_path, {'robot': llm}), llm


def heavy(v1):
    """A 1.2 m + 1.1 m arm carrying 20 kg: far beyond a 2020/2040 extrusion and a printed mount."""
    p = {**v1.robot.params, 'upper_arm_m': 1.2, 'forearm_m': 1.1, 'upper_arm_mass_kg': 8.0, 'forearm_mass_kg': 6.0,
         'payload_kg': 20.0, 'joint_limits_deg': [], 'max_speed_deg_s': [], 'max_torque_nm': []}
    return v1.model_copy(update={'robot': v1.robot.model_copy(update={'params': p}),
                                 'sim': v1.sim.model_copy(update={'frames': []})})


async def test_light_case_keeps_the_proposal(v1, tmp_path):
    reg, llm = agent(tmp_path, 'extrusion_printed')
    lines = []
    out = await st.choose_style(reg, v1, [], lines.append)
    assert out.style == 'extrusion_printed' and out.proposed == 'extrusion_printed' and not out.forced
    assert out.reasons == ['light payload'] and out.checks and all(c.passed is not False for c in out.checks)
    assert {c.requirement_id for c in out.checks} >= {'style_upper_arm_bending', 'style_upper_arm_deflection',
                                                     'style_j2_printed_bearing', 'style_j2_print_envelope'}
    assert llm.calls and lines


async def test_light_case_hand_calculated_bending(v1):
    p = v1.robot.params
    L1 = p['upper_arm_m']
    s = {x.joint: x for x in sz.size_joints(v1)}
    M = s['j2'].static_torque + s['j2'].accel_torque
    checks = {c.requirement_id: c for c in st.style_checks(v1, 'extrusion_printed', [])}
    bend, defl = checks['style_upper_arm_bending'], checks['style_upper_arm_deflection']
    I, c = st.EXTRUSIONS['2020']                                    # the smallest section passes here
    assert 'extrusion' in bend.detail and bend.detail.startswith('2020')
    assert bend.actual == pytest.approx(M * c / I / 1e6, rel=1e-6) and bend.passed
    assert defl.actual == pytest.approx(M * L1 ** 2 / (3 * 69e9 * I) * 1000, rel=1e-6)
    assert defl.required == pytest.approx(L1 / 200 * 1000)
    # printed mount: F = T / r_b with the default 66.7 mm bolt circle, sigma = F / (4 d t)
    j2 = checks['style_j2_printed_bearing']
    assert j2.actual == pytest.approx(M / (0.0667 / 2) / (4 * 0.005 * 0.008) / 1e6, rel=1e-6)
    assert checks['style_j2_print_envelope'].actual == pytest.approx(56.4 + 30)


async def test_long_heavy_link_forces_machined_aluminium(v1, tmp_path):
    reg, _ = agent(tmp_path, 'extrusion_printed', ('cheap and easy to build',))
    out = await st.choose_style(reg, heavy(v1), [], lambda t: None)
    assert out.proposed == 'extrusion_printed' and out.style == 'machined_aluminium' and out.forced
    assert out.reasons == ['cheap and easy to build']               # the agent's words are kept, not edited
    failing = [c for c in out.checks if c.passed is False]
    assert failing and all(not c.requirement_id.startswith('machined_') for c in failing)
    assert {c.requirement_id for c in failing} >= {'style_upper_arm_deflection', 'style_j2_printed_bearing'}
    machined = [c for c in out.checks if c.requirement_id.startswith('machined_style_')]
    assert machined and any('tube' in c.detail for c in machined)


async def test_machined_proposal_is_kept_without_forcing(v1, tmp_path):
    reg, _ = agent(tmp_path, 'machined_aluminium')
    out = await st.choose_style(reg, heavy(v1), [], lambda t: None)
    assert out.style == 'machined_aluminium' and not out.forced and out.proposed == 'machined_aluminium'
    assert all(c.requirement_id.startswith('machined_') for c in out.checks)


async def test_invalid_agent_answer_is_retried(v1, tmp_path):
    answers = iter([{'style': 'carbon', 'reasons': []}, {'style': 'extrusion_printed', 'reasons': ['ok']}])
    llm = ScriptedLLM([('structure style', lambda *a: next(answers))])
    out = await st.choose_style(LLMRegistry(tmp_path, {'robot': llm}), v1, [], lambda t: None)
    assert out.style == 'extrusion_printed' and len(llm.calls) == 2


async def test_gantry_beam_formulas(v1):
    params = {'payload_kg': 10.0, 'x_moving_mass_kg': 12.0, 'y_moving_mass_kg': 6.0, 'z_moving_mass_kg': 3.0,
              'tool_mass_kg': 0.5, 'x_stroke_m': 1.0, 'y_stroke_m': 0.8, 'z_stroke_m': 0.5, 'z_offset_m': 0.1}
    v = v1.model_copy(update={'robot': RobotConcept(family='gantry', name='g', params=params),
                              'sim': v1.sim.model_copy(update={'frames': []})})
    checks = {c.requirement_id: c for c in st.style_checks(v, 'machined_aluminium', [])}
    F = (6 + 3 + 0.5 + 10) * G                                       # carried by the X beam at mid-span
    L = 1.2
    name = next(c.detail for c in checks.values() if c.requirement_id == 'machined_style_x_beam_bending').split(',')[0]
    a, t = (float(x) / 1000 for x in name.split(' ')[0].split('x')[0::2])   # '30x30x2 tube' -> side, wall
    I, c = st._tube(a, t)
    assert checks['machined_style_x_beam_bending'].actual == pytest.approx(F * L / 4 * c / I / 1e6, rel=1e-6)
    assert checks['machined_style_x_beam_deflection'].actual == pytest.approx(F * L ** 3 / (48 * 69e9 * I) * 1000, rel=1e-6)
    assert 'machined_style_z_column_bending' in checks


async def test_mobile_base_has_no_beams(v1):
    v = v1.model_copy(update={'robot': RobotConcept(family='mobile_base', name='b', params={})})
    checks = st.style_checks(v, 'extrusion_printed', [])
    assert checks and all('printed' in c.requirement_id or 'print_envelope' in c.requirement_id for c in checks)
    with pytest.raises(ValueError):
        st.style_checks(v, 'wood', [])
