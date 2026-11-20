"""Robot studio build pipeline (pipeline/studio.py) and the four studio_* tasks, with scripted (fixture) LLM answers,
the saved shop pages in tests/fixtures/shops/ and a fake download. Fixture results, not model results, no network;
CAD tests skip without OpenCascade."""
import asyncio
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip('OCP')

from fixtures_specs import pallet_spec  # noqa: E402
from studio_build_scripts import (PAGES, SEARCH_MATCH, SHOPS, SOL, STYLE_MATCH, fake_download_map,  # noqa: E402
                                  motor_answer, style_answer)
from studio_fixtures import make_version  # noqa: E402
from test_tasks import environment, fixture, profiles, request, run_task  # noqa: E402

from studio import families, physics, sim  # noqa: E402
from studio.catalog.fetch import Downloaded  # noqa: E402
from studio.llm.registry import LLMRegistry  # noqa: E402
from studio.llm.scripted import ScriptedLLM  # noqa: E402
from studio.pipeline import studio as sp  # noqa: E402
from studio.schemas import DesignVersion, RobotConcept, SceneSpec, StudioBuild  # noqa: E402
from studio.tasks.handlers import version_json  # noqa: E402

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


async def fake_download(url, max_bytes=0, timeout_s=0):
    if url not in PAGES:
        raise ValueError('404')
    return Downloaded((SHOPS / PAGES[url]).read_bytes(), 'text/html', url, 'page.html')


def registry(tmp_path, style='extrusion_printed', search=None):
    search = search or ScriptedLLM([(SEARCH_MATCH, motor_answer())])
    robot = ScriptedLLM([(STYLE_MATCH, style_answer(style))])
    return LLMRegistry(tmp_path, {'search': search, 'robot': robot}), search, robot


def run_build(reg, version, tmp_path, name='out', member=None, base=None, request='', pinned=None, no=1):
    out = tmp_path / name
    return asyncio.run(sp.build(reg, version, member, base, request, pinned or {}, no, out, lambda t: None,
                                download=fake_download, now=lambda: NOW)), out


def gantry_version() -> DesignVersion:
    spec = pallet_spec()
    base = physics.place_base(spec)
    reqs = physics.derive(spec, base)
    fam = families.get('gantry')
    p = fam.size_from_physics(reqs, spec, {})
    concept = RobotConcept(family='gantry', name='g', params=p.model_dump(), base=base)
    scene = SceneSpec(boxes=[], robot_base=base)
    res = sim.simulate(spec, reqs, scene, concept)
    return DesignVersion(version=1, created='2026-11-01T10:00:00Z', spec=spec, requirements=reqs, scene=scene,
                         robot=concept, urdf=fam.build_urdf(p, 'g'), sim=res, approved=True)


@pytest.fixture(scope='module')
def arm():
    return make_version()


@pytest.fixture(scope='module')
def b1(arm, tmp_path_factory):
    tmp = tmp_path_factory.mktemp('b1')
    reg, search, robot = registry(tmp)
    build, out = run_build(reg, arm, tmp)
    return build, out, search, robot


def test_b1_arm_end_to_end(b1, arm):
    build, out, search, robot = b1
    StudioBuild.model_validate(build.model_dump(mode='json'))
    assert (build.build, build.design_version, build.family, build.parent_build, build.member) == (1, arm.version, 'serial_arm', None, None)
    assert build.search_date == '2026-11-20' and build.created == '2026-11-20T09:00:00Z' and build.request == ''
    assert len(search.calls) >= 1 and len(robot.calls) == 1
    assert [m.joint for m in build.motors] == [s.joint for s in build.sizing]
    shop = [m for m in build.motors if m.source == 'shop']
    assert shop, 'at least one joint is served by a saved shop page'
    for m in build.motors:
        assert m.source in ('shop', 'defined') and (m.alternative_id is not None) == (m.source == 'shop')
    m = shop[0]
    assert m.shop == 'robu' and m.price.seen == '2026-11-20' and m.price.amount == 2499
    assert any(v.status == 'verified' and v.quote for v in m.values) and len(m.alternatives) == 2
    assert build.electronics and any(e.role == 'controller' for e in build.electronics)
    assert build.style.style == 'extrusion_printed' and build.parts and build.checks
    assert 'motors from shops' in build.summary and 'checks passed' in build.summary
    assert 'meshes/' in build.urdf and 'gripper' in build.urdf and build.twin_limits.gripper is not None
    for f in build.files:
        assert (out / f.path).is_file() and f.sha256 and f.bytes
    assert (out / 'assembly.step').is_file() and (out / 'urdf/robot.urdf').is_file()
    cad_checks = [c for c in build.checks if c.requirement_id.startswith('cad_')]
    assert len(cad_checks) >= 4 and all(c.passed for c in cad_checks), [c for c in cad_checks if not c.passed]


def test_pin_only_rebuild_makes_no_search(b1, arm, tmp_path):
    base, _, _, _ = b1
    weak = next(a.id for a in base.motors[0].alternatives if 'stepperonline' in a.id)
    cold = ScriptedLLM([])                                        # any search call raises AssertionError
    reg, search, robot = registry(tmp_path, search=cold)
    build, _ = run_build(reg, arm, tmp_path, base=base, pinned={'j1': weak}, no=2)
    assert cold.calls == []
    j1 = build.motors[0]
    assert j1.alternative_id == weak and j1.pinned and j1.source == 'shop' and j1.name == next(
        a.name for a in base.motors[0].alternatives if a.id == weak)
    assert build.parent_build == 1 and build.build == 2 and build.search_date == base.search_date
    assert [m.alternative_id for m in build.motors[1:]] == [m.alternative_id for m in base.motors[1:]]


def test_gantry_build(tmp_path):
    reg, search, _ = registry(tmp_path)
    build, out = run_build(reg, gantry_version(), tmp_path)
    assert build.family == 'gantry' and [s.joint for s in build.sizing][:3] == ['x_axis', 'y_axis', 'z_axis']
    assert build.motors and build.parts and (out / 'assembly.step').is_file()
    assert {e.role for e in build.electronics} >= {'driver', 'controller', 'power_supply'}


def test_request_reaches_the_style_and_search_agents(arm, tmp_path):
    reg, search, robot = registry(tmp_path, style='machined_aluminium')
    build, _ = run_build(reg, arm, tmp_path, request='use machined aluminium')
    assert build.request == 'use machined aluminium' and build.style.style == 'machined_aluminium'
    assert 'use machined aluminium' in robot.calls[0]['input']
    assert 'use machined aluminium' in search.calls[0]['input']
    assert build.summary.endswith('Change: use machined aluminium')


def test_mobile_manipulator_is_not_in_v1(arm, tmp_path):
    other = arm.model_copy(update={'robot': arm.robot.model_copy(update={'family': 'mobile_manipulator'})})
    reg, _, _ = registry(tmp_path)
    with pytest.raises(ValueError, match='not in v1'):
        run_build(reg, other, tmp_path)


# -- the tasks as subprocesses ---------------------------------------------------------------------------------------

@pytest.fixture(scope='module')
def tasks(arm, tmp_path_factory):
    tmp = tmp_path_factory.mktemp('tasks')
    rules = [{'match': SEARCH_MATCH, 'answers': [motor_answer()]}, {'match': STYLE_MATCH, 'answers': [style_answer()]}]
    llm = profiles(fixture(tmp, rules))
    env = environment(STUDIO_FAKE_DOWNLOAD=str(fake_download_map(tmp)))
    build_run = run_task('studio_build', request('studio_build', {'version': version_json(arm), 'member': None, 'base_build': None,
                                                                  'request': '', 'pinned': {}, 'build': 1}, tmp, llm), env=env)
    return tmp, llm, env, build_run


def test_studio_build_task(tasks):
    _, _, _, run = tasks
    assert run.code == 0, run.stderr
    assert run.lines[-1]['type'] == 'result' and not run.of('error')
    assert any(p['text'] == 'Generating the CAD' for p in run.of('progress'))
    data = run.result
    build = StudioBuild.model_validate(data['build'])
    assert build.build == 1 and data['parsed']['joints'] and any(m.source == 'shop' for m in build.motors)
    assert {c['role'] for c in run.calls} >= {'search', 'robot'}
    assert (run.out_dir / 'assembly.step').is_file()


def test_studio_twin_and_run_task(tasks, arm, tmp_path):
    _, _, _, run = tasks
    build = run.result['build']
    q0 = {j['joint']: 0.0 for j in build['twin_limits']['joints']}
    move = run_task('studio_twin_move', request('studio_twin_move', {'build': build, 'version': version_json(arm), 'request': {
        'q0': q0, 'target': {'joints': {'j1': 0.3}}, 'speed_scale': 1.0}}, tmp_path))
    assert move.code == 0, move.stderr
    assert move.result['frames'] and move.result['reached'] is True
    ran = run_task('studio_run_task', request('studio_run_task', {'build': build, 'version': version_json(arm), 'speed_scale': 1.0},
                                              tmp_path))
    assert ran.code == 0, ran.stderr
    assert ran.result['frames'] and 'torque_use' in ran.result
    bad = run_task('studio_run_task', request('studio_run_task', {'build': build, 'version': version_json(arm), 'speed_scale': 9},
                                              tmp_path))
    assert bad.code == 1 and bad.error['kind'] == 'value'


def test_studio_pdf_task(tasks, arm, tmp_path):
    _, _, _, run = tasks
    body = {'build': run.result['build'], 'version': version_json(arm), 'project': {'id': 'p_0123456789', 'title': 'Bottle',
                                                                                    'brief': 'Move a bottle'},
            'build_dir': str(run.out_dir), 'snapshots': []}
    pdf = run_task('studio_pdf', request('studio_pdf', body, tmp_path))
    assert pdf.code == 0, pdf.stderr
    data = pdf.result
    assert data['pdf'] == 'manufacturer.pdf' and data['package'] == 'package.zip'
    assert {f['path'] for f in data['files']} == {'manufacturer.pdf', 'bom.csv', 'package.zip'}
    out = pdf.out_dir
    assert (out / 'manufacturer.pdf').read_bytes()[:4] == b'%PDF' and not (out / '_stage').exists()
    with zipfile.ZipFile(out / 'package.zip') as z:
        names = z.namelist()
    assert {'manufacturer.pdf', 'bom.csv', 'assembly.step', 'urdf/robot.urdf'} <= set(names)
    assert (run.out_dir / 'manufacturer.pdf').exists() is False      # the build folder was only read


def test_java_rules_file_matches_the_scripts():
    root = Path(__file__).resolve().parents[1] / 'server-java/src/test/resources/scripted'
    import importlib.util
    spec = importlib.util.spec_from_file_location('make_studio_fixtures', root / 'make_studio_fixtures.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert json.loads((root / 'rules_studio.json').read_text()) == {'rules': json.loads(json.dumps(mod.rules()))}
