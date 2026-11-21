"""The Gemini CLI adapter, against a fake `gemini` executable (no real CLI, no network, no model call)."""
import json
import os
import stat
import sys
import time

import pytest

from studio.llm import registry
from studio.llm.base import LLMError, supports_images
from studio.llm.gemini_cli import BUILT_IN_TOOLS, GeminiCLI, NO_SUCH, find_cli

FAKE = '''#!{python}
import json, os, subprocess, sys, time
spec = json.load(open({spec!r}))
stdin = sys.stdin.read()
cwd = os.getcwd()
settings_path = os.environ.get('GEMINI_CLI_SYSTEM_SETTINGS_PATH', '')
record = {{'argv': sys.argv[1:], 'env': dict(os.environ), 'cwd': cwd, 'stdin': stdin,
          'cwd_listing': sorted(os.listdir(cwd)),
          'project_settings': json.load(open(os.path.join(cwd, '.gemini', 'settings.json'))),
          'system_settings_path': settings_path,
          'system_md': open(os.environ['GEMINI_SYSTEM_MD']).read() if os.environ.get('GEMINI_SYSTEM_MD') else None}}
json.dump(record, open({log!r}, 'w'))
if spec.get('hang'):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    open({log!r} + '.child', 'w').write(str(child.pid))
    time.sleep(60)
sys.stderr.write(spec.get('stderr', ''))
if 'stdout' in spec:
    sys.stdout.write(spec['stdout'])
sys.exit(spec.get('code', 0))
'''

STATS = {'models': {'gemini-3.8-flash': {'api': {'totalRequests': 1}, 'tokens': {
    'input': 800, 'prompt': 1000, 'candidates': 50, 'total': 1100, 'cached': 200, 'thoughts': 50, 'tool': 0}}},
    'tools': {'totalCalls': 2, 'byName': {'google_web_search': {'count': 2}}}, 'files': {}}


def answer(response='hello', stats=None, **extra):
    return json.dumps({'session_id': 's', 'response': response, 'stats': STATS if stats is None else stats, **extra})


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'AIza-secret-key-for-test')
    monkeypatch.setenv('GOOGLE_API_KEY', 'AIza-other-secret-key')
    log = tmp_path / 'log.json'
    spec = tmp_path / 'spec.json'
    script = tmp_path / 'gemini'

    def make(**behaviour):
        spec.write_text(json.dumps({'stdout': answer(), **behaviour}))
        script.write_text(FAKE.format(python=sys.executable, spec=str(spec), log=str(log)))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return str(script)

    make.log = lambda: json.loads(log.read_text())
    make.child_file = str(log) + '.child'
    return make


async def test_plain_call_is_locked_down_and_keyless(fake):
    adapter = GeminiCLI(model='gemini-3.8-flash', cli_path=fake())
    assert await adapter.invoke('long prompt ' * 2000, 'You are strict.') == 'hello'
    seen = fake.log()
    argv, env = seen['argv'], seen['env']
    assert argv[argv.index('-m') + 1] == 'gemini-3.8-flash' and argv[argv.index('-o') + 1] == 'json'
    assert argv[argv.index('--approval-mode') + 1] == 'default' and 'yolo' not in ' '.join(argv) and '-p' not in argv
    assert not any('long prompt' in a or 'AIza' in a for a in argv)               # the prompt goes on stdin only
    assert seen['stdin'] == 'long prompt ' * 2000
    assert not any('AIza' in v for v in env.values()) and 'GEMINI_API_KEY' not in env and 'GOOGLE_API_KEY' not in env
    assert seen['system_md'] == 'You are strict.' and os.path.realpath(env['GEMINI_SYSTEM_MD']) == os.path.join(seen['cwd'], 'system-prompt.md')
    tools = seen['project_settings']['tools']
    assert tools['core'] == [NO_SUCH] and tools['allowed'] == []                   # no tool at all
    assert set(tools['exclude']) == set(BUILT_IN_TOOLS) and 'run_shell_command' in tools['exclude'] and 'write_file' in tools['exclude']
    assert seen['project_settings']['mcp']['allowed'] == [NO_SUCH] and seen['project_settings']['skills']['enabled'] is False
    assert os.path.realpath(seen['system_settings_path']) == os.path.join(seen['cwd'], '.gemini', 'settings.json')
    assert sorted(seen['cwd_listing']) == ['.gemini', 'system-prompt.md']          # a fresh, empty working folder
    assert env['GEMINI_CLI_TRUST_WORKSPACE'] == 'true'
    assert not os.path.exists(seen['cwd'])                                        # removed after the call


async def test_web_calls_keep_only_the_google_web_tools(fake):
    adapter = GeminiCLI(cli_path=fake()).with_web()
    assert adapter.web and adapter.timeout_s == 900
    await adapter.invoke('find robots', 'research')
    seen = fake.log()
    tools = seen['project_settings']['tools']
    assert tools['core'] == ['google_web_search', 'web_fetch'] and tools['allowed'] == ['google_web_search', 'web_fetch']
    assert 'run_shell_command' in tools['exclude'] and 'write_file' in tools['exclude'] and 'web_fetch' not in tools['exclude']
    assert 'google_web_search' in seen['system_md'] and 'yolo' not in ' '.join(seen['argv'])
    assert adapter.last_call['web_calls'] == 2


async def test_usage_is_parsed_and_reported(fake):
    entries = []
    adapter = GeminiCLI(model='gemini-3.8-flash', cli_path=fake())
    adapter.on_usage = entries.append
    await adapter.invoke('hi', 's')
    (entry,) = entries
    assert (entry['input'], entry['cache_read'], entry['output'], entry['cache_write']) == (800, 200, 100, 0)
    assert entry['cost_usd'] == round((800 * 0.75 + 100 * 3.75 + 200 * 0.075) / 1e6, 6)
    assert entry['ok'] is True and entry['model'] == 'gemini-3.8-flash' and entry['images'] == 0 and entry['web_calls'] == 2
    assert set(entry) >= {'seconds', 'turns', 'structured', 'pages_opened'}


async def test_unknown_model_has_no_cost_and_missing_stats_are_fine(fake):
    adapter = GeminiCLI(cli_path=fake(stdout=answer(stats={'models': {'gemini-future': {'tokens': {'prompt': 10, 'candidates': 4}}}})))
    await adapter.invoke('hi', 's')
    assert adapter.last_call['input'] == 10 and adapter.last_call['output'] == 4 and adapter.last_call['cost_usd'] is None
    adapter = GeminiCLI(cli_path=fake(stdout=json.dumps({'response': 'x'})))
    assert await adapter.invoke('hi', 's') == 'x' and adapter.last_call['input'] == 0


async def test_warnings_before_the_json_are_ignored(fake):
    adapter = GeminiCLI(cli_path=fake(stdout='Loaded cached credentials.\n' + answer('fine')))
    assert await adapter.invoke('hi', 's') == 'fine'


@pytest.mark.parametrize('behaviour, expected', [
    ({'stdout': json.dumps({'error': {'type': 'ApiError', 'message': 'Quota exceeded (429)'}}), 'code': 1}, 'quota'),
    ({'stdout': '', 'stderr': 'Please sign in: not logged in', 'code': 1}, 'not signed in'),
    ({'stdout': json.dumps({'response': 'x', 'error': {'message': 'Model not found 404'}}), 'code': 1}, 'model name'),
    ({'stdout': '', 'code': 53}, 'turn limit'),
    ({'stdout': '', 'stderr': 'boom', 'code': 1}, 'boom'),
    ({'stdout': json.dumps({'response': '  ', 'stats': {}})}, 'no text'),
])
async def test_errors_become_llm_errors(fake, behaviour, expected):
    with pytest.raises(LLMError) as raised:
        await GeminiCLI(cli_path=fake(**behaviour)).invoke('hi', 's')
    assert expected in str(raised.value).lower()
    assert 'AIza' not in str(raised.value)


async def test_timeout_kills_the_whole_process_tree(fake):
    adapter = GeminiCLI(cli_path=fake(hang=True), timeout_s=3)
    started = time.monotonic()
    with pytest.raises(LLMError, match='timed out'):
        await adapter.invoke('hi', 's')
    assert time.monotonic() - started < 15
    child = int(open(fake.child_file).read())
    for _ in range(50):                                      # the sleeping grandchild must be gone, not just the CLI
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(child, 9)
        pytest.fail('the CLI\'s child process survived the timeout')


def test_no_images_and_no_native_schema():
    adapter = GeminiCLI()
    assert not supports_images(adapter) and not hasattr(adapter, 'structured') and not hasattr(adapter, 'invoke_multimodal')


def test_missing_cli_has_a_helpful_message(tmp_path):
    with pytest.raises(LLMError, match='not found at'):
        find_cli(str(tmp_path / 'nope'))


def test_registered_with_defaults():
    assert 'gemini_cli' in registry.PROVIDERS
    adapter = registry.gemini_cli({'model': 'gemini-3.8-flash', 'timeout_s': 60, 'cli_path': ''})
    assert isinstance(adapter, GeminiCLI) and adapter.timeout_s == 60
    ids = {p['id']: p for p in registry.default_profiles()}
    assert {ids[i]['kind'] for i in ('gemini_pro', 'gemini_flash', 'gemini_flash_lite')} == {'gemini'}
    assert {ids[i]['kind'] for i in ('gemini_cli_pro', 'gemini_cli_flash')} == {'gemini_cli'}
    assert set(registry.DEFAULT_ROLES.values()) <= set(ids) and registry.DEFAULT_ROLES['objects'] == 'gemini_flash_lite'
    assert {ids[i]['kind'] for i in ('model_a_pro', 'model_a_fast', 'model_a_lite')} == {'claude_cli'}
    assert not set(registry.DEFAULT_ROLES.values()) & {'model_a_pro', 'model_a_fast', 'model_a_lite'}
