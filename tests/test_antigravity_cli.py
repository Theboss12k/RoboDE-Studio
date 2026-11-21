"""The Antigravity CLI adapter, against a fake `agy` executable (no real CLI, no network, no model call)."""
import json
import os
import stat
import sys
import time

import pytest

from studio.llm import registry
from studio.llm.antigravity_cli import DENIED, MODELS, AntigravityCLI, find_cli
from studio.llm.base import LLMError, supports_images

FAKE = '''#!{python}
import json, os, subprocess, sys, time
spec = json.load(open({spec!r}))
stdin = sys.stdin.read()
cwd = os.getcwd()
agent_path = os.path.join(cwd, '.agents', 'agents', 'studio', 'agent.md')
record = {{'argv': sys.argv[1:], 'env': dict(os.environ), 'cwd': cwd, 'stdin': stdin,
          'listing': sorted(os.listdir(cwd)), 'agent': open(agent_path).read() if os.path.exists(agent_path) else None}}
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

USAGE = {'input_tokens': 1000, 'output_tokens': 120, 'thinking_tokens': 70, 'cache_read_tokens': 400, 'total_tokens': 1120}


def stream(response='hello', usage=None, steps=(), **extra):
    result = {'conversation_id': 'c', 'status': 'SUCCESS', 'response': response, 'duration_seconds': 1.0, 'num_turns': 1,
              'usage': USAGE if usage is None else usage, **extra}
    events = [{'event': 'init', 'conversation_id': 'c', 'init': {'cwd': '/x', 'tools': ['run_command']}}, *steps,
              {'event': 'result', 'result': result}]
    return ''.join(json.dumps(e) + '\n' for e in events)


def tool_step(name, **parameters):
    return {'event': 'step_update', 'step_update': {'step_index': 2, 'state': 'DONE', 'step_type': 'tool', 'tool_name': name,
                                                    'tool_info': {'name': name, 'parameters': parameters}}}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    for name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'STUDIO_TOKEN', 'STUDIO_CALLBACK_SECRET'):
        monkeypatch.setenv(name, 'AIza-secret-for-test')
    log = tmp_path / 'log.json'
    spec = tmp_path / 'spec.json'
    script = tmp_path / 'agy'

    def make(**behaviour):
        spec.write_text(json.dumps({'stdout': stream(), **behaviour}))
        script.write_text(FAKE.format(python=sys.executable, spec=str(spec), log=str(log)))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return str(script)

    make.log = lambda: json.loads(log.read_text())
    make.child_file = str(log) + '.child'
    return make


async def test_plain_call_is_locked_down_and_keyless(fake):
    adapter = AntigravityCLI(model='gemini-3.8-flash-low', effort='high', cli_path=fake())
    assert await adapter.invoke('long prompt ' * 2000, 'You are strict.') == 'hello'
    seen = fake.log()
    argv, env = seen['argv'], seen['env']
    assert argv[argv.index('--model') + 1] == 'gemini-3.8-flash-low' and argv[argv.index('--effort') + 1] == 'high'
    assert argv[argv.index('--output-format') + 1] == 'stream-json' and argv[argv.index('--input-format') + 1] == 'stream-json'
    assert argv[argv.index('--agent') + 1] == 'studio' and '--disable-slash-commands' in argv
    assert '--dangerously-skip-permissions' not in argv and '--mode' not in argv and '-p' not in argv and '--print' not in argv
    assert '--json-schema' not in argv
    assert not any('long prompt' in a or 'AIza' in a for a in argv)                  # the prompt goes on stdin only
    assert json.loads(seen['stdin']) == {'event': 'user', 'message': {'content': 'long prompt ' * 2000}}
    for name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'STUDIO_TOKEN', 'STUDIO_CALLBACK_SECRET'):
        assert name not in env
    assert not any('AIza' in v for v in env.values())
    config = json.loads(argv[argv.index('--settings') + 1])
    assert config['permissions']['deny'] == list(DENIED) and 'allow' not in config['permissions']
    assert 'command(*)' in DENIED and 'mcp(*)' in DENIED and 'write_file(*)' in DENIED and 'read_url(*)' in DENIED
    assert config['toolPermission'] == 'strict' and config['agentMode'] == '' and config['enableTerminalSandbox'] is True
    agent = seen['agent']
    assert agent.startswith('---\nname: studio\n') and 'tools:\n  - view_file\n---\nYou are strict.' in agent
    assert 'search_web' not in agent and 'run_command' not in agent and 'finish' not in agent
    assert seen['listing'] == ['.agents']                                           # a fresh, empty working folder
    assert not os.path.exists(seen['cwd'])                                          # removed after the call


async def test_no_effort_flag_without_effort(fake):
    await AntigravityCLI(cli_path=fake()).invoke('hi', 's')
    argv = fake.log()['argv']
    assert '--effort' not in argv and argv[argv.index('--model') + 1] == 'gemini-3.8-flash-medium'


async def test_web_calls_keep_only_the_web_tools(fake):
    adapter = AntigravityCLI(cli_path=fake(stdout=stream(steps=[tool_step('search_web', query='x'),
                                                                tool_step('read_url_content', Url='https://example.com')]))).with_web()
    assert adapter.web and adapter.timeout_s == 900
    await adapter.invoke('find robots', 'research')
    seen = fake.log()
    argv = seen['argv']
    config = json.loads(argv[argv.index('--settings') + 1])
    assert config['permissions']['allow'] == ['read_url(*)'] and 'read_url(*)' not in config['permissions']['deny']
    assert {'command(*)', 'write_file(*)', 'read_file(*)', 'mcp(*)'} <= set(config['permissions']['deny'])
    assert 'tools:\n  - search_web\n  - read_url_content\n---\n' in seen['agent'] and 'view_file' not in seen['agent']
    assert '--dangerously-skip-permissions' not in argv
    assert adapter.last_call['web_calls'] == 2 and adapter.last_call['pages_opened'] == ['https://example.com']


async def test_structured_passes_the_schema_and_returns_the_object(fake):
    schema = {'type': 'object', 'properties': {'n': {'type': 'integer'}}, 'required': ['n']}
    adapter = AntigravityCLI(cli_path=fake(stdout=stream('{"n":3}\n', structured_output={'n': 3}))).structured(schema)
    assert json.loads(await adapter.invoke('give n', 's')) == {'n': 3}
    seen = fake.log()
    argv = seen['argv']
    assert json.loads(argv[argv.index('--json-schema') + 1]) == schema
    assert 'tools:\n  - view_file\n  - finish\n---\n' in seen['agent']                 # `finish` carries the structured answer
    assert adapter.last_call['structured'] is True


async def test_usage_is_parsed_and_reported(fake):
    entries = []
    adapter = AntigravityCLI(model='gemini-3.8-flash-high', cli_path=fake())
    adapter.on_usage = entries.append
    await adapter.invoke('hi', 's')
    (entry,) = entries
    assert (entry['input'], entry['cache_read'], entry['output'], entry['thinking'], entry['cache_write']) == (600, 400, 120, 70, 0)
    assert entry['cost_usd'] is None and entry['ok'] is True and entry['model'] == 'gemini-3.8-flash-high'
    assert entry['images'] == 0 and entry['turns'] == 1 and set(entry) >= {'seconds', 'structured', 'web_calls', 'pages_opened'}


async def test_missing_usage_is_fine(fake):
    adapter = AntigravityCLI(cli_path=fake(stdout=json.dumps({'event': 'result', 'result': {'status': 'SUCCESS', 'response': 'x\n'}}) + '\n'))
    assert await adapter.invoke('hi', 's') == 'x' and adapter.last_call['input'] == 0


def error(message):
    return json.dumps({'event': 'result', 'result': {'conversation_id': '', 'status': 'ERROR', 'response': '', 'error': message,
                                                      'num_turns': 0, 'usage': {}}}) + '\n'


@pytest.mark.parametrize('behaviour, expected', [
    ({'stdout': error('quota exceeded (429)'), 'code': 1}, 'quota'),
    ({'stdout': '', 'stderr': 'authentication required', 'code': 1}, 'not signed in'),
    ({'stdout': error('invalid model selection: model x is not recognized as a known model')}, 'model name'),
    ({'stdout': '', 'stderr': 'boom', 'code': 1}, 'boom'),
    ({'stdout': error('something odd')}, 'something odd'),
    ({'stdout': stream('  ')}, 'no text'),
])
async def test_errors_become_llm_errors(fake, behaviour, expected):
    with pytest.raises(LLMError) as raised:
        await AntigravityCLI(cli_path=fake(**behaviour)).invoke('hi', 's')
    assert expected in str(raised.value).lower()
    assert 'AIza' not in str(raised.value)


async def test_error_calls_are_still_counted_as_not_ok(fake):
    entries = []
    adapter = AntigravityCLI(cli_path=fake(stdout=error('bad')))
    adapter.on_usage = entries.append
    with pytest.raises(LLMError):
        await adapter.invoke('hi', 's')
    assert entries[0]['ok'] is False


async def test_oversized_input_is_refused_before_starting(fake):
    with pytest.raises(LLMError, match='too large'):
        await AntigravityCLI(cli_path=fake()).invoke('x' * (9 * 1024 * 1024), 's')


async def test_timeout_kills_the_whole_process_tree(fake):
    adapter = AntigravityCLI(cli_path=fake(hang=True), timeout_s=3)
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
        pytest.fail("the CLI's child process survived the timeout")


def test_no_images_and_native_schema():
    adapter = AntigravityCLI()
    assert not supports_images(adapter) and hasattr(adapter, 'structured') and not hasattr(adapter, 'invoke_multimodal')


def test_missing_cli_has_a_helpful_message(tmp_path):
    with pytest.raises(LLMError, match='not found at'):
        find_cli(str(tmp_path / 'nope'))


def test_find_cli_falls_back_to_the_installer_folder(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    (home / '.gemini' / 'bin').mkdir(parents=True)
    script = home / '.gemini' / 'bin' / 'agy'
    script.write_text('#!/bin/sh\n')
    script.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path / 'empty'))
    monkeypatch.setattr('studio.llm.antigravity_cli._home', lambda: str(home))
    assert find_cli() == str(script)


def test_registered_with_defaults():
    assert 'antigravity_cli' in registry.PROVIDERS
    adapter = registry.antigravity_cli({'model': 'gemini-3.8-flash-low', 'timeout_s': 60, 'effort': 'low', 'cli_path': ''})
    assert isinstance(adapter, AntigravityCLI) and adapter.timeout_s == 60 and adapter.effort == 'low'
    ids = {p['id']: p for p in registry.default_profiles()}
    assert {ids[i]['kind'] for i in ('antigravity_flash_high', 'antigravity_flash_medium', 'antigravity_flash_low')} == {'antigravity_cli'}
    assert [ids[i]['model'] for i in ('antigravity_flash_high', 'antigravity_flash_medium', 'antigravity_flash_low')] == list(MODELS)
    assert set(registry.DEFAULT_ROLES.values()) <= set(ids) and not any(v.startswith('antigravity') for v in registry.DEFAULT_ROLES.values())
