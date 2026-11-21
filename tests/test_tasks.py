"""The Java <-> Python task protocol (docs/PROTOCOL.md), run as real subprocesses with scripted (fixture) LLMs.

No network and no real model: every LLM answer comes from a fixture file via the `scripted` profile kind, which the
tasks accept only with STUDIO_ALLOW_SCRIPTED=1. Results here are fixture results, never model results.
"""
import asyncio
import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from studio.agents import revise as revise_agent
from studio.llm.base import LLMError
from studio.schemas import DesignVersion
from studio.tasks import handlers, protocol
from studio.tasks.llms import TaskLLMs
from studio_scripts import ROBOT, llms as scripted_llms, scene_dict, spec_dict

ROOT = Path(__file__).resolve().parents[1]
ROLES = ('spec', 'scene', 'robot', 'revise', 'search', 'extract', 'analysis', 'objects', 'ask', 'checks')
KEY = 'SECRET-API-KEY-abc123'


def environment(**extra):
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'server'), 'STUDIO_ALLOW_SCRIPTED': '1'}
    env.pop('GEMINI_API_KEY', None)
    env.update(extra)
    return {k: v for k, v in env.items() if v is not None}


def fixture(tmp_path, rules, name='rules.json'):
    path = tmp_path / name
    path.write_text(json.dumps({'rules': rules}))
    return path


def profiles(path, api_key=None):
    profile = {'id': 'fx', 'kind': 'scripted', 'fixture': str(path), 'model': 'fixture', 'api_key': api_key}
    return {'roles': {role: profile for role in ROLES}, 'profile': profile}


def request(task, input, tmp_path, llm=None, **extra):
    out = tmp_path / 'out'
    out.mkdir(exist_ok=True)
    return {'protocol': 1, 'task': task, 'input': input, 'llm': llm or {'roles': {}, 'profile': None},
            'context': {'project_id': 'p_0123456789', 'task': task}, 'out_dir': str(out), **extra}


class Run:
    def __init__(self, proc, out_dir):
        self.proc, self.out_dir = proc, Path(out_dir)
        self.stdout, self.stderr, self.code = proc.stdout, proc.stderr, proc.returncode
        self.lines = [json.loads(line) for line in proc.stdout.splitlines()]

    def of(self, kind):
        return [line for line in self.lines if line['type'] == kind]

    @property
    def result(self):
        (line,) = self.of('result')
        return json.loads((self.out_dir / line['file']).read_text()) if 'file' in line else line['data']

    @property
    def error(self):
        (line,) = self.of('error')
        return line

    @property
    def calls(self):
        log = self.out_dir / 'llm_calls.jsonl'
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def run_task(task, body, env=None, args=None):
    proc = subprocess.run([sys.executable, *(args or ['-m', 'studio.tasks', task])], input=json.dumps(body), text=True,
                          capture_output=True, cwd=ROOT, env=env or environment(), timeout=180)
    return Run(proc, body['out_dir'])


def bottle_rules(extra=()):
    return [{'match': 'requirements engineer', 'answers': [{'spec': spec_dict()}]},
            {'match': '3D scene', 'answers': [scene_dict()]},
            {'match': 'kind of robot', 'answers': [ROBOT]}, *extra]


@pytest.fixture(scope='module')
def v1(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('design')
    run = run_task('design', request('design', {'brief': 'Move a 0.3 kg bottle from A to B', 'reference': None, 'answers': {}, 'round': 0},
                                     tmp, profiles(fixture(tmp, bottle_rules()))))
    assert run.code == 0, run.stderr
    return run


def test_meta(tmp_path):
    run = run_task('meta', request('meta', {}, tmp_path))
    assert run.code == 0 and run.lines[-1]['type'] == 'result'
    data = run.result
    assert {f['name'] for f in data['families']} >= {'serial_arm', 'gantry', 'mobile_base', 'mobile_manipulator'}
    assert data['roles'] == list(ROLES) and {'antigravity_cli', 'claude_cli', 'gemini', 'gemini_cli', 'vertex_gemini'} <= set(data['kinds']) and 'gemini_cli' in data['models']
    assert data['default_roles']['spec'] == 'gemini_pro' and data['default_roles']['objects'] == 'gemini_flash_lite'
    assert {p['id'] for p in data['default_profiles']} == {'gemini_pro', 'gemini_flash', 'gemini_flash_lite', 'vertex_pro', 'vertex_flash', 'vertex_flash_lite', 'gemini_cli_pro', 'gemini_cli_flash', 'antigravity_flash_high', 'antigravity_flash_medium', 'antigravity_flash_low'}
    assert set(data['default_roles'].values()) <= {p['id'] for p in data['default_profiles']}


def test_design_protocol_lines(v1):
    assert v1.code == 0 and len(v1.of('result')) == 1 and not v1.of('error')
    assert v1.lines[-1]['type'] == 'result' and any('Sizing' in p['text'] for p in v1.of('progress'))
    data = v1.result      # inline, or from result.json when above the inline limit
    assert data['status'] == 'designed' and data['round'] == 1
    version = DesignVersion.model_validate(data['version'])
    assert version.version == 0 and version.parent_version is None and version.sim.frames
    assert data['parsed']['name'] and data['parsed']['joints']
    assert {c['role'] for c in v1.calls} == {'spec', 'scene', 'robot'}


def test_design_questions_then_round(tmp_path):
    q = {'questions': [{'id': 'cycle', 'text': 'Cycle time?', 'kind': 'number', 'unit': 's'}]}
    rules = [{'match': 'requirements engineer', 'answers': [q, {'spec': spec_dict()}]}] + bottle_rules()[1:]
    llm = profiles(fixture(tmp_path, rules))
    first = run_task('design', request('design', {'brief': 'bottle', 'reference': None, 'answers': {}, 'round': 0}, tmp_path, llm))
    assert first.code == 0 and first.result['status'] == 'questions' and first.result['round'] == 1
    assert first.result['questions'][0]['id'] == 'cycle'
    second = run_task('design', request('design', {'brief': 'bottle', 'reference': None, 'answers': {'cycle': 6}, 'round': 1},
                                        tmp_path, profiles(fixture(tmp_path, bottle_rules(), 'second.json'))))   # a new process starts a new fixture
    assert second.result['status'] == 'designed' and second.result['round'] == 2
    spec_call = [c for c in second.calls if c['role'] == 'spec'][-1]
    assert '"cycle":6' in spec_call['input'] or '"cycle": 6' in spec_call['input']


def test_revise_from_old_version_with_moment(v1, tmp_path):
    base1 = v1.result['version']
    base1['version'] = 1
    old = DesignVersion.model_validate(base1)
    robot = {**base1['robot'], 'params': {**base1['robot']['params'], 'tool_length_m': 0.2}}
    assert old.robot.params['tool_length_m'] != 0.2
    patch = {'targets': ['robot'], 'robot': robot, 'summary': 'Longer tool'}
    llm = profiles(fixture(tmp_path, [{'match': 'change an existing', 'answers': [patch]}]))
    # v2 exists (revised from v1, no moment); now revise v1 again at t = 2.0 s
    v2 = run_task('revise', request('revise', {'base': base1, 'request': 'use a 20 cm tool', 't': None}, tmp_path, llm))
    assert v2.code == 0 and v2.result['version']['parent_version'] == 1
    v2_version = {**v2.result['version'], 'version': 2}
    assert v2_version['robot']['params']['tool_length_m'] == 0.2
    out = tmp_path / 'second'
    out.mkdir()
    body = request('revise', {'base': base1, 'request': 'make the arm longer here', 't': 2.0}, tmp_path, llm, out_dir=str(out))
    run = run_task('revise', body)
    assert run.code == 0, run.stderr
    version = run.result['version']
    assert version['parent_version'] == 1 and version['change_request'] == 'make the arm longer here'
    assert run.result['parsed']['joints']
    prompt = run.calls[-1]['input']
    for key, value in old.robot.params.items():                       # v1's params, not v2's
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            assert f'"{key}":{json.dumps(value)}' in prompt
    assert '"tool_length_m":0.2' not in prompt
    reach = revise_agent.capabilities(old.robot)['reach_max_m']
    assert f'"reach_max_m":{reach}' in prompt and '"payload_kg"' in prompt and '"repeatability_m"' in prompt
    frame = min(old.sim.frames, key=lambda f: abs(f.t - 2.0))
    assert abs(frame.t - 2.0) < 0.2
    assert json.dumps(frame.phase) in prompt
    assert json.dumps([round(c, 4) for c in frame.tcp], separators=(',', ':')) in prompt
    assert '"moment"' in prompt and '"checks"' in prompt and '"requirement_targets"' in prompt
    assert '"passed":' in prompt and 'the moment on screen' in run.calls[-1]['system_prompt']


def test_revise_t_out_of_range_is_value_error(v1, tmp_path):
    base = v1.result['version']
    llm = profiles(fixture(tmp_path, bottle_rules()))
    run = run_task('revise', request('revise', {'base': base, 'request': 'x', 't': base['sim']['duration_s'] + 5}, tmp_path, llm))
    assert run.code == 1 and run.error['kind'] == 'value' and 'between 0 and' in run.error['message'] and not run.of('result')


def test_view_without_moment_has_everything():
    version = DesignVersion.model_validate(_bottle_version())
    view = revise_agent.view(version)
    assert 'moment' not in view and view['checks'] and view['requirement_targets']
    assert {c['passed'] for c in view['checks']} <= {True, False, None}
    assert view['robot']['capabilities']['reach_max_m'] > 0 and view['robot']['params'] and view['spec']['title']
    moment = revise_agent.view(version, 0.0)['moment']
    assert moment['frame']['t'] == 0.0 and 'joints' in moment['frame'] and 'events' in moment


def _bottle_version():
    from studio.pipeline import design as pipeline
    from studio.pipeline.design import design
    from fixtures_specs import bottle_spec

    class Registry:
        def for_role(self, role):
            return scripted_llms()[role]
    return asyncio.run(design(Registry(), bottle_spec(), lambda t: None)).model_dump(mode='json')


def test_error_mapping(v1, tmp_path):
    unknown = run_task('nope', request('nope', {}, tmp_path))
    assert unknown.code == 1 and unknown.error['kind'] == 'value'
    missing = run_task('inspect_upload', request('inspect_upload', {'path': str(tmp_path / 'none.urdf'), 'kind': 'urdf'}, tmp_path))
    assert missing.error['kind'] == 'not_found' and missing.code == 1
    bad = tmp_path / 'bad.urdf'
    bad.write_text('<robot><oops')
    assert run_task('inspect_upload', request('inspect_upload', {'path': str(bad), 'kind': 'urdf'}, tmp_path)).error['kind'] == 'value'
    # an LLM that never returns JSON -> LLMError -> llm
    junk = profiles(fixture(tmp_path, [{'match': 'requirements engineer', 'answers': ['this is not json']}], 'junk.json'))
    llm_run = run_task('design', request('design', {'brief': 'b', 'answers': {}, 'round': 0}, tmp_path, junk))
    assert llm_run.error['kind'] == 'llm' and llm_run.code == 1
    # no matching scripted answer -> internal (traceback goes to stderr)
    none = profiles(fixture(tmp_path, [{'match': 'zzz', 'answers': ['x']}], 'none.json'))
    internal = run_task('design', request('design', {'brief': 'b', 'answers': {}, 'round': 0}, tmp_path, none))
    assert internal.error['kind'] == 'internal' and 'Traceback' in internal.stderr
    # datasheet failure carries its code
    cand = {'id': 'acme_x', 'manufacturer': 'Acme', 'model': 'X', 'family': 'serial_arm', 'product_url': ''}
    version = {**v1.result['version'], 'version': 1}
    ds = run_task('analyse', request('analyse', {'version': version, 'candidate': cand, 'datasheet_url': None, 'document_path': None},
                                     tmp_path, profiles(fixture(tmp_path, bottle_rules()))))
    assert ds.error['kind'] == 'value' and ds.error['code'] == 'datasheet_download'
    # scripted profiles are refused unless STUDIO_ALLOW_SCRIPTED=1
    refused = run_task('llm_test', request('llm_test', {}, tmp_path, profiles(fixture(tmp_path, bottle_rules()))),
                       env=environment(STUDIO_ALLOW_SCRIPTED=None))
    assert refused.error['kind'] == 'value' and 'tests' in refused.error['message']


def test_stray_prints_do_not_corrupt_stdout(tmp_path):
    driver = ('import os, sys\n'
              'from studio.tasks import handlers, main\n'
              'real = handlers.HANDLERS["meta"]\n'
              'async def noisy(inp, ctx):\n'
              '    print("stray print")\n'
              '    sys.stdout.write("more noise\\n")\n'
              '    os.write(1, b"raw fd write\\n")\n'
              '    return await real(inp, ctx)\n'
              'handlers.HANDLERS["meta"] = noisy\n'
              'sys.exit(main(["meta"]))\n')
    run = run_task('meta', request('meta', {}, tmp_path), args=['-c', driver])
    assert run.code == 0 and [l['type'] for l in run.lines][-1] == 'result'      # every stdout line parsed as JSON
    assert all('noise' not in line and 'stray' not in line and 'raw fd' not in line for line in run.stdout.splitlines())
    assert 'stray print' in run.stderr and 'raw fd write' in run.stderr


def test_api_key_never_leaks(tmp_path):
    rules = bottle_rules()
    llm = profiles(fixture(tmp_path, rules), api_key=KEY)
    run = run_task('design', request('design', {'brief': 'bottle', 'answers': {}, 'round': 0}, tmp_path, llm))
    assert run.code == 0
    assert KEY not in run.stdout and KEY not in run.stderr
    assert all(KEY not in p.read_text(errors='ignore') for p in run.out_dir.iterdir())
    # even when a task fails with the key in its message, the error line is redacted
    driver = ('import sys\nfrom studio.tasks import handlers, main\n'
              'async def boom(inp, ctx):\n'
              '    ctx.progress("using " + ctx.llms.secrets()[0])\n'
              '    raise ValueError("bad key " + ctx.llms.secrets()[0])\n'
              'handlers.HANDLERS["llm_test"] = boom\nsys.exit(main(["llm_test"]))\n')
    failed = run_task('llm_test', request('llm_test', {}, tmp_path, llm), args=['-c', driver])
    assert failed.code == 1 and KEY not in failed.stdout and '[redacted]' in failed.error['message']


def test_inspect_upload_and_export_assets(v1, tmp_path):
    version = v1.result['version']
    urdf = tmp_path / 'robot.urdf'
    urdf.write_text(version['urdf'])
    run = run_task('inspect_upload', request('inspect_upload', {'path': str(urdf), 'kind': 'urdf'}, tmp_path))
    assert run.result['ok'] and run.result['summary']['moving_joints']
    doc = tmp_path / 'sheet.md'
    doc.write_text('Rated payload 6 kg')
    assert run_task('inspect_upload', request('inspect_upload', {'path': str(doc), 'kind': 'datasheet'}, tmp_path)).result == {'ok': True, 'text_chars': 18}
    png = tmp_path / 'x.png'
    png.write_bytes(__import__('base64').b64decode(handlers.TEST_PNG))
    assert run_task('inspect_upload', request('inspect_upload', {'path': str(png), 'kind': 'image'}, tmp_path)).result == {'ok': True, 'mime': 'image/png'}
    out = tmp_path / 'assets'
    out.mkdir()
    exported = run_task('export_assets', request('export_assets', {'urdf': version['urdf'], 'want': ['parsed', 'glb', 'stl']},
                                                 tmp_path, out_dir=str(out)))
    assert exported.code == 0 and exported.result['parsed']['joints']
    assert (out / 'design.glb').read_bytes()[:4] == b'glTF' and (out / 'design.stl').stat().st_size > 0


def test_large_results_go_to_a_file_and_heartbeat(tmp_path):
    stream = io.StringIO()
    emitter = protocol.Emitter(stream, heartbeat_s=0.05)
    emitter.start_heartbeat()
    threading.Event().wait(0.3)
    emitter.stop_heartbeat()
    emitter.result({'blob': 'x' * (protocol.RESULT_INLINE_MAX + 10)}, tmp_path)
    emitter.result({'small': 1}, tmp_path)
    lines = [json.loads(l) for l in stream.getvalue().splitlines()]
    assert sum(l['type'] == 'heartbeat' for l in lines) >= 2
    assert lines[-2] == {'type': 'result', 'file': 'result.json'} and lines[-1] == {'type': 'result', 'data': {'small': 1}}
    assert len(json.loads((tmp_path / 'result.json').read_text())['blob']) > protocol.RESULT_INLINE_MAX


def test_usage_lines_come_from_the_adapter_callback(tmp_path):
    stream = io.StringIO()
    emitter = protocol.Emitter(stream)
    taskllms = TaskLLMs({'spec': {'id': 'claude_opus', 'kind': 'claude_cli', 'model': 'claude-opus-5-5'}}, emitter.usage, tmp_path)
    adapter = taskllms.for_role('spec')
    adapter.on_usage({'model': 'claude-opus-5-5', 'input': 10, 'output': 5, 'pages_opened': 3, 'cost_usd': 0.01})
    (line,) = [json.loads(l) for l in stream.getvalue().splitlines()]
    assert line == {'type': 'usage', 'role': 'spec', 'profile': 'claude_opus',
                    'entry': {'model': 'claude-opus-5-5', 'input': 10, 'output': 5, 'cost_usd': 0.01}}
    assert list(tmp_path.iterdir()) == []                                  # no ledger or other file writes
    with pytest.raises(ValueError):
        taskllms.for_role('scene')                                         # role without a profile
    with pytest.raises(ValueError, match='unknown provider'):
        TaskLLMs({'spec': {'kind': 'mystery'}}).for_role('spec')


def test_error_classification():
    c = protocol.classify
    assert c(ValueError('x')) == ('value', None)
    assert c(FileNotFoundError('x')) == ('not_found', None)
    assert c(KeyError('LLM profile p not found')) == ('not_found', None)
    assert c(KeyError('other')) == ('internal', None)
    assert c(LLMError('x')) == ('llm', None)
    assert c(RuntimeError('x')) == ('internal', None)


DATASHEET = (Path(__file__).parent / 'fixtures' / 'arm_datasheet.md').read_text()


def analysis_reply(input, system, images):
    items = json.loads(input.split('Differences (JSON):\n', 1)[1])
    return {'differences': [{**d, 'impact': f'{d["topic"]}: fine for this task.'} for d in items]}


def handler_overrides():
    """Scripted answers for the search, extract, analysis and revise roles used by the find/analyse handlers."""
    from studio.llm.scripted import ScriptedLLM
    from test_catalog import answer as search_answer, values_answer
    kw = scripted_llms()
    kw['search'] = ScriptedLLM([('find real robots', search_answer())])
    kw['extract'] = ScriptedLLM([('manufacturer\'s datasheet', values_answer())])
    kw['analysis'] = ScriptedLLM([('explain, in plain', analysis_reply)])
    return kw


async def test_find_and_analyse_handlers_with_fake_download(v1, tmp_path):
    """find + analyse through the task handlers (in process): scripted search/extract/analysis, a fake download."""
    from studio.catalog.fetch import Downloaded
    kw = handler_overrides()

    class Fixed(TaskLLMs):
        def for_role(self, role):
            return kw[role]

    async def download(url):
        return Downloaded(bytes=DATASHEET.encode(), content_type='text/markdown', final_url=url, filename='ra6.md')
    out = tmp_path / 'o'
    out.mkdir()
    ctx = handlers.Context(Fixed({}), out, lambda t: None, download=download)
    version = {**v1.result['version'], 'version': 1}
    found = await handlers.find({'version': version, 'limit': 6}, ctx)
    assert found['candidates'] and 'dropped' in found
    cand = next(c for c in found['candidates'] if c['id'] == 'acme_ra_6')
    done = await handlers.analyse({'version': version, 'candidate': cand, 'datasheet_url': 'https://acme.example/ra6.md', 'document_path': None}, ctx)
    assert done['analysis']['candidate_id'] == 'acme_ra_6' and done['parsed']['joints']
    assert done['document'] == {'file': 'document.txt', 'url': 'https://acme.example/ra6.md', 'title': 'ra6.md'}
    assert 'payload' in (out / 'document.txt').read_text().lower()
    uploaded = tmp_path / 'upload.md'
    uploaded.write_text(DATASHEET)
    again = await handlers.analyse({'version': version, 'candidate': cand, 'datasheet_url': None, 'document_path': str(uploaded)}, ctx)
    assert again['document'] is None and again['analysis']['manufacturer_spec']['values']


def test_ask_task_records_proposals_and_never_acts(tmp_path):
    """The `ask` task through the real subprocess entry point with a scripted fixture (fixture result, not a model result)."""
    rules = [{'match': 'Ask bar', 'answers': [
        {'tool': 'search', 'args': {}},
        {'final': {'answer': 'Queued a search.', 'citations': [{'kind': 'version', 'ref': '7', 'label': 'v7'}], 'confirm': [], 'navigate': None}}]}]
    grounding = {'project': {'id': 'p_0123456789', 'versions': [{'version': 1}]}}
    run = run_task('ask', request('ask', {'question': 'Find robots', 'history': [], 'context': {'project_id': 'p_0123456789'},
                                          'grounding': grounding}, tmp_path, profiles(fixture(tmp_path, rules))))
    assert run.code == 0, run.stderr
    assert run.result == {'answer': 'Queued a search.', 'citations': [], 'confirm': [], 'navigate': None,
                          'proposals': [{'kind': 'search', 'args': {}}]}
    empty = run_task('ask', request('ask', {'question': ' '}, tmp_path, profiles(fixture(tmp_path, rules))))
    assert empty.error['kind'] == 'value'
