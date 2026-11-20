"""The fleet pipeline (pipeline/fleet.py, the fleet tasks and agents) with scripted LLMs.

No model is called: every answer comes from tests/fleet_scripts.py (the same rules as the Java fixture
rules_fleet.json), so these are fixture tests of the pipeline logic, not model evaluations. Sizes, runs and checks are
real code: kinematic and static-load calculations, not dynamics simulation and not hardware tests.
"""
import asyncio
import json
import time
from pathlib import Path

import pytest
import fleet_scripts as fs
from studio_scripts import llms as bottle_llms

from studio.llm.base import JSON_REPAIRS, LLMError
from studio.pipeline import catalog as catalog_pipeline
from studio.pipeline import design as pl
from studio.pipeline import fleet as fp
from studio.schemas import DesignVersion, FleetAssignment, SimEvent
from studio.tasks import handlers
from studio.tasks.llms import FixtureLLM
from test_tasks import DATASHEET, analysis_reply, profiles, request, run_task

RULES_FILE = Path(__file__).resolve().parents[1] / 'server-java/src/test/resources/scripted/rules_fleet.json'
HEARTBEAT_GAP_S = 20.0


class Fixture:
    """Like TaskLLMs with a scripted profile: one FixtureLLM per role over one shared rule list."""

    def __init__(self, rules, extra=()):
        self.rules = [(r['match'], list(r['answers']), 0.0) for r in [*extra, *rules]]
        self.adapters = {}

    def for_role(self, role):
        return self.adapters.setdefault(role, FixtureLLM(self.rules, role, None))

    def calls(self, role):
        return self.adapters[role].calls if role in self.adapters else []


class Lines:
    """Progress sink that keeps (monotonic time, text)."""

    def __init__(self):
        self.items = []

    def __call__(self, text):
        self.items.append((time.monotonic(), text))

    @property
    def texts(self):
        return [t for _, t in self.items]

    def max_gap(self, start):
        stamps = [start, *[s for s, _ in self.items]]
        return max(b - a for a, b in zip(stamps, stamps[1:]))


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope='module')
def v1():
    """The single-robot design of the carton task (version 1), as the design task makes it."""
    llms = Fixture(fs.rules())
    step = run(pl.specify(llms, fs.BRIEF, None, {}, 1, lambda t: None))
    assert step['status'] == 'spec' and step['fleet'].robots_needed == 3
    return run(pl.design(llms, step['spec'], lambda t: None)).model_copy(update={'version': 1})


@pytest.fixture(scope='module')
def planned(v1):
    """(fleet version 2, the fixture LLMs, progress lines, start time) for the automatic assignment."""
    llms, lines = Fixture(fs.rules()), Lines()
    start = time.monotonic()
    v = run(fp.design_fleet(llms, v1, fs.LIMITS, None, lines))
    return v.model_copy(update={'version': 2}), llms, lines, start


def checks_by_id(checks):
    return {c.requirement_id: c for c in checks}


# -- fixture file ----------------------------------------------------------------------------------------------------

def test_java_fixture_matches_the_scripts():
    assert json.loads(RULES_FILE.read_text()) == {'rules': fs.rules()}


# -- trigger -------------------------------------------------------------------------------------------------------------

async def test_design_task_gives_fleet_advice_for_the_long_task_only(tmp_path):
    lines = Lines()
    ctx = handlers.Context(Fixture(fs.rules()), tmp_path, lines)
    out = await handlers.design({'brief': fs.BRIEF, 'reference': None, 'answers': {}, 'round': 0}, ctx)
    advice = out['fleet_advice']
    assert advice['source'] == 'both' and advice['robots_needed'] == 3
    assert advice['reasons'][0].startswith('Spec agent (a proposal): A and B are 5 m apart')
    assert any(r.startswith('Code: ') and 'practical 2.5 m' in r for r in advice['reasons'])
    flagged = [t for t in lines.texts if t.startswith('One robot may not be enough: ')]
    assert len(flagged) == 1 and lines.texts[-1] == flagged[0]
    assert 'fleet' not in out['version']

    class Bottle:
        def __init__(self):
            self.kw = bottle_llms()

        def for_role(self, role):
            return self.kw[role]
    quiet = Lines()
    out = await handlers.design({'brief': 'Move a bottle', 'reference': None, 'answers': {}, 'round': 0},
                                handlers.Context(Bottle(), tmp_path, quiet))
    assert 'fleet_advice' not in out and not any('One robot' in t for t in quiet.texts)
    assert set(out) == {'status', 'version', 'parsed', 'round'}


def test_code_alone_flags_a_long_task(v1):
    advice = pl.fleet_advice(v1, None)
    assert advice['source'] == 'code' and advice['robots_needed'] >= 2


# -- assignment preview ----------------------------------------------------------------------------------------------------

def test_assign_preview(v1):
    out = fp.assign(v1, fs.LIMITS)
    json.dumps(out)
    a = FleetAssignment.model_validate(out['assignment'])
    assert [m.actions for m in a.members] == [[0], [], [1]] and len(a.handovers) == 2
    assert all(h.position is not None and not h.fixed for h in a.handovers)
    assert out['feasible'] is True and out['notes'][0] == 'Automatic split: 3 robots, 2 handover(s).'
    assert any(n.startswith('r1: sized reach serial arm') and 'your limit 1.30 m' in n for n in out['notes'])
    p = out['preview']
    assert set(p) == {'bases', 'handovers', 'parks', 'workspace_samples'}
    assert set(p['bases']) == set(p['parks']) == set(p['workspace_samples']) == {'r1', 'r2', 'r3'}
    assert set(p['bases']['r1']) == {'position', 'yaw'} and set(p['parks']['r2']) == {'start', 'end'}
    assert set(p['handovers']) == {'h1', 'h2'} and len(p['handovers']['h1']) == 3
    assert all(0 < len(s) <= fp.MAX_WORKSPACE_SAMPLES and len(s[0]) == 3 for s in p['workspace_samples'].values())
    tight = fp.assign(v1, fs.LIMITS.model_copy(update={'max_reach_m': 0.9}))
    assert tight['feasible'] is False and any('no allowed type fits' in n for n in tight['notes'])
    mine = out['assignment'] | {'source': 'user', 'confirmed': True}
    assert fp.assign(v1, fs.LIMITS, mine)['notes'][0].startswith('Your split')


def test_fleet_assign_task_errors_and_result(v1, tmp_path):
    body = {'version': v1.model_dump(mode='json'), 'limits': fs.LIMITS.model_dump(mode='json')}
    bad = {'members': [{'id': 'r1', 'actions': [0]}, {'id': 'r2', 'actions': []}], 'handovers': []}
    r = run_task('fleet_assign', request('fleet_assign', {**body, 'assignment': bad}, tmp_path))
    assert r.code == 1 and r.error['kind'] == 'value' and 'r2 has no actions' in r.error['message']
    r = run_task('fleet_assign', request('fleet_assign', {**body, 'assignment': None}, tmp_path))
    assert r.code == 0 and set(r.result) == {'assignment', 'preview', 'feasible', 'notes'}


# -- fleet design ------------------------------------------------------------------------------------------------------------

def test_fleet_design_end_to_end(planned, v1):
    v, llms, lines, start = planned
    f = v.fleet
    assert len(f.members) >= 2 and [m.id for m in f.members] == ['r1', 'r2', 'r3'] and f.primary == 'r1'
    data = handlers.version_json(v)
    assert 'fleet' in data and 'repair' not in data and DesignVersion.model_validate(data).fleet is not None
    assert v.parent_version == 1 and f.task == v1.spec and f.limits == fs.LIMITS and f.trigger == ['user']
    for m in f.members:                                   # every member within the user's reach limit, by code
        c = checks_by_id(m.limit_checks)
        assert m.robot.family == 'gantry' and c['limit_reach'].passed and c['limit_reach'].actual <= 1.3
        assert m.sim.checks and m.robot.base.position == m.spec.marker('robot_base').position
    fc = checks_by_id(f.checks)
    assert set(fc) == {'fleet_robot_count', 'fleet_base_spacing', 'fleet_handover_reach', 'fleet_robot_clearance',
                       'fleet_cycle_time'}
    assert fc['fleet_handover_reach'].passed, fc['fleet_handover_reach'].detail
    assert all(c.passed for c in f.checks) and f.ok
    assert f.members[0].t_offset_s == 0 < f.members[1].t_offset_s < f.members[2].t_offset_s
    assert f.duration_s == pytest.approx(f.members[2].t_offset_s + f.members[2].sim.duration_s)
    assert v.spec == f.members[0].spec and v.robot == f.members[0].robot and v.urdf == f.members[0].urdf
    assert {b.id for b in v.scene.boxes} >= {'conveyor_a', 'conveyor_b', 'station_h1', 'station_h2'}
    assert v.scene.robot_base == f.members[0].robot.base
    assert all(h.position is not None for h in f.assignment.handovers)
    assert v.change_summary == ('Multi-robot layout: 3 robots, 2 handovers; 5 of 5 fleet checks pass and 3 of 3 '
                                'robots pass their own checks.')
    assert any(line.startswith('Re-choice round 1: r1: limit_reach fails') for line in f.planner_log)


def test_rechoice_prompt_carries_the_codes_reasons(planned):
    _, llms, lines, _ = planned
    robot_calls = llms.calls('robot')
    r1 = [c for c in robot_calls if 'Fleet member r1.' in c['input']]
    assert len(r1) == 2 and 'Feedback from code' not in r1[0]['input']
    fb = r1[1]['input'].split('Feedback from code on the previous choice:\n', 1)[1]
    assert 'Your previous choice for r1: serial_arm ("Carton arm")' in fb
    assert 'limit_reach fails: code sized this serial arm to a reach of 1.41 m; the user allows at most 1.30 m' in fb
    assert 'by robot type: serial_arm 1.41 m, gantry 1.21 m' in fb
    assert all('"name":"mobile_base"' not in c['input'] for c in robot_calls)     # fixed-base families only
    assert any(t.startswith('Choosing again for r1 (round 1 of 2): limit_reach fails') for t in lines.texts)


def test_heartbeat_progress_lines(planned):
    """Progress lines come at least every HEARTBEAT_GAP_S (the Java watchdog kills a task after 60 s of silence):
    each member's sizing, the re-choice and the schedule emit their own line."""
    _, _, lines, start = planned
    assert lines.max_gap(start) < HEARTBEAT_GAP_S
    sizing = [t for t in lines.texts if t.startswith('Sizing r')]
    assert sizing == ['Sizing r1: serial arm for its share of the task', 'Sizing r2: gantry for its share of the task',
                      'Sizing r3: gantry for its share of the task', 'Sizing r1: gantry for its share of the task']
    assert 'Scheduling the robots on a shared clock and checking robot-robot clearance' in lines.texts


def test_user_assignment_is_not_changed(v1):
    """A confirmed assignment whose robots fail stays as it is: failures are reported as checks."""
    a = FleetAssignment.model_validate(fp.assign(v1, fs.LIMITS)['assignment'])
    two = FleetAssignment(members=[a.members[0].model_copy(update={'family': 'serial_arm'}),
                                   a.members[2].model_copy(update={'id': 'r2', 'family': 'serial_arm'})],
                          handovers=[a.handovers[0].model_copy(update={'to_member': 'r2', 'position': None})],
                          source='user', confirmed=True)
    arm = {'match': 'kind of robot', 'answers': [fs.MEMBER_ARM]}
    v = run(fp.design_fleet(Fixture(fs.rules(), [arm]), v1, fs.LIMITS, two, lambda t: None, auto_repair=False))
    assert [m.id for m in v.fleet.members] == ['r1', 'r2'] and v.fleet.assignment.source == 'user'
    assert all(checks_by_id(m.limit_checks)['limit_reach'].passed is False for m in v.fleet.members)
    assert not v.fleet.ok and any(line.startswith('Still failing') for line in v.fleet.planner_log)


def test_fleet_design_task_through_the_protocol(v1, tmp_path):
    llm = profiles(RULES_FILE)
    body = {'base': v1.model_dump(mode='json'), 'limits': fs.LIMITS.model_dump(mode='json'), 'assignment': None}
    r = run_task('fleet_design', request('fleet_design', body, tmp_path, llm))
    assert r.code == 0, r.stderr
    out = r.result
    assert set(out) == {'version', 'parsed', 'fleet_parsed'} and set(out['fleet_parsed']) == {'r1', 'r2', 'r3'}
    assert out['version']['fleet']['primary'] == 'r1' and out['parsed']['joints']
    assert {c['role'] for c in r.calls} == {'scene', 'robot'}
    texts = [p['text'] for p in r.of('progress')]
    assert texts[0] == 'Planning 3 robots for "Carton transfer"' and texts[-1].startswith('Multi-robot layout: 3 robots')


# -- revise --------------------------------------------------------------------------------------------------------------------

async def test_fleet_revise_patch_replans_and_rechecks(planned):
    v2 = planned[0]
    llms, lines = Fixture(fs.rules()), Lines()
    v3 = await pl.revise(llms, v2, 'give r2 a rotating tool and 10 cm beams', 3.0, lines)
    f = v3.fleet
    assert v3.parent_version == 2 and v3.change_request.startswith('give r2')
    r2 = next(m for m in f.members if m.id == 'r2')
    assert r2.robot.family == 'gantry' and r2.robot.params['tool_yaw'] is True and r2.robot.params['beam_size_m'] == 0.1
    assert 'tool_yaw' in r2.urdf and r2.sim.checks and r2.limit_checks
    assert [m.robot.family for m in f.members] == ['gantry'] * 3 and len(f.checks) == 5
    assert v3.change_summary.startswith('r2 gets a tool yaw axis and wider beams. Multi-robot layout: 3 robots')
    assert 'Re-planning the robots (r2 changed)' in lines.texts and 'Keeping the scene and the robots' in lines.texts
    view = json.loads(llms.calls('revise')[0]['input'].split('Version on screen (JSON):\n', 1)[1].split('\n\nUser')[0])
    assert view['spec']['title'] == 'Carton transfer' and len(view['fleet']['members']) == 3
    assert set(view['moment']['members']) == {'r1', 'r2', 'r3'} and view['fleet']['members'][1]['actions'] == []
    assert not llms.calls('robot') and not llms.calls('scene')        # unchanged robots and scene are kept


async def test_fleet_revise_moves_a_station_and_reports_the_reach(planned):
    v2 = planned[0]
    move = {'match': 'change an existing', 'answers': [{'targets': ['fleet'], 'summary': 'Station h1 moved 20 cm.',
                                                        'fleet': {'handovers': [{'id': 'h1', 'position': [-1.033, 0, 0.3]}]}}]}
    v3 = await pl.revise(Fixture(fs.rules(), [move]), v2, 'move h1 20 cm towards A', None, lambda t: None)
    h1 = next(h for h in v3.fleet.assignment.handovers if h.id == 'h1')
    assert h1.fixed and h1.position == (-1.033, 0.0, 0.3)
    assert next(m for m in v3.fleet.members if m.id == 'r2').spec.marker('h1').position == (-1.033, 0.0, 0.3)
    reach = {m.id: checks_by_id(m.limit_checks)['limit_reach'] for m in v3.fleet.members}
    assert reach['r2'].actual > checks_by_id(v2.fleet.members[1].limit_checks)['limit_reach'].actual   # code re-sized r2


async def test_robot_target_is_rejected_on_a_fleet_version(planned):
    v2 = planned[0]
    robot = {'match': 'change an existing', 'answers': [{'targets': ['robot'], 'robot': fs.MEMBER_GANTRY, 'summary': 'x'}]}
    llms = Fixture(fs.rules(), [robot])
    with pytest.raises(LLMError):
        await pl.revise(llms, v2, 'make it a gantry', None, lambda t: None)
    calls = llms.calls('revise')
    # every rejected answer is fed back (ask_json: 1 call + JSON_REPAIRS repairs)
    assert len(calls) == 1 + JSON_REPAIRS and 'This design has several robots: change them under "fleet"' in calls[1]['input']


# -- member views ----------------------------------------------------------------------------------------------------------

async def test_member_view_with_analyse(planned, v1):
    from test_catalog import answer as search_answer, values_answer
    from studio.catalog.fetch import Downloaded
    v2 = planned[0]
    r2 = fp.member_view(v2, 'r2')
    assert r2.spec == v2.fleet.members[1].spec and r2.scene.robot_base == v2.fleet.members[1].robot.base
    assert fp.member_view(v2, None) is v2 and fp.member_view(v1, 'r1') is v1
    with pytest.raises(KeyError, match='not found'):
        fp.member_view(v2, 'r7')
    with pytest.raises(ValueError, match='one robot'):
        fp.member_view(v1, 'r2')
    llms = Fixture([{'match': 'find real robots', 'answers': [search_answer()]},
                    {'match': "manufacturer's datasheet", 'answers': [values_answer()]}])
    from studio.llm.scripted import ScriptedLLM
    llms.adapters['analysis'] = ScriptedLLM([('explain, in plain', analysis_reply)])

    async def download(url):
        return Downloaded(bytes=DATASHEET.encode(), content_type='text/markdown', final_url=url, filename='ra6.md')
    cand = (await catalog_pipeline.find(llms, v2, lambda t: None, download, member='r2'))['candidates'][0]
    out = await catalog_pipeline.analyse(llms, v2, cand, 'https://acme.example/ra6.md', lambda t: None, download,
                                         member='r2')
    a = out['analysis']
    assert a.robot.base == v2.fleet.members[1].robot.base
    assert any(f.phase == 'pick carton at handover h1' for f in a.sim.frames)          # r2's own sub-task


# -- robot-robot collision repair -------------------------------------------------------------------------------------------

async def test_robot_robot_collision_triggers_the_repair(planned):
    """A robot-robot contact is injected into the plan (the scripted cell is clear); the repair loop asks the
    revise agent, code re-plans and re-checks, and keeps the clear result."""
    v2 = planned[0]
    f = v2.fleet
    event = SimEvent(t=9.0, kind='collision', text='Robot r1 segment 1 collides with robot r2 segment 1 during "x" / "y"')
    checks = [c.model_copy(update={'passed': False, 'detail': '1 contact episode(s); one item on the shared clock'})
              if c.requirement_id == 'fleet_robot_clearance' else c for c in f.checks]
    hit = v2.model_copy(update={'fleet': f.model_copy(update={'events': [event], 'checks': checks, 'ok': False})})
    assert pl.collision_episodes(hit) == 1 and pl.collision_episodes(v2) == 0
    fix = {'match': 'Automatic repair', 'answers': [{'targets': ['fleet'], 'summary': 'Moved r3 a little.',
                                                     'fleet': {'members': [{'id': 'r3', 'base': [1.7, 0.0, 0.0]}]}}]}
    llms, lines = Fixture(fs.rules(), [fix]), Lines()
    out = await pl.repair_collisions(llms, hit, lines)
    assert out.repair is not None and out.repair.kept == 1 and out.repair.stop_reason == 'collision free'
    assert pl.collision_episodes(out) == 0 and out.fleet.members[2].robot.base.position == (1.7, 0.0, 0.0)
    request_text = llms.calls('revise')[0]['input'].split('User request:\n', 1)[1]
    assert request_text.startswith('Automatic repair (attempt 1 of 3)') and 'Robot r1 segment 1 collides' in request_text
    assert 'move a robot\'s base (fleet members[].base)' in request_text
    assert lines.texts[0] == 'Collisions found (1): automatic fix, attempt 1 of 3'
    assert any(t.startswith('Attempt 1: Laying out the cell') for t in lines.texts)    # inner lines keep the job alive
    assert out.change_summary.endswith('Automatic collision repair: kept attempt 1 of 1 (0 collision(s) left).')

