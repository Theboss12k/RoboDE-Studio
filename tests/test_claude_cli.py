"""Claude CLI adapter: executable discovery, the reduced environment, and one run against a stub CLI.
No network and no real CLI: the stub is a small Python script started through the same code path."""
import asyncio
import base64
import os
import sys
import textwrap

import pytest

from studio.llm import claude_cli
from studio.llm.base import Image, LLMError
from studio.llm.claude_cli import ClaudeCLI, cli_environment, find_cli

windows_only = pytest.mark.skipif(os.name != 'nt', reason='searches Windows install locations')


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'')
    return str(path)


@pytest.fixture
def no_path_cli(monkeypatch, tmp_path):
    """Nothing on PATH, and an empty home, so only the locations a test creates can be found."""
    monkeypatch.setattr(claude_cli.shutil, 'which', lambda name: None)
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setattr(claude_cli, '_home', lambda: str(home))
    monkeypatch.setenv('APPDATA', str(tmp_path / 'roaming'))
    return tmp_path


@windows_only
def test_find_cli_picks_newest_version_folder(no_path_cli, monkeypatch):
    local = no_path_cli / 'local'
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    base = local / 'Claude-Data' / 'profile' / 'claude-code'
    _touch(base / '1.9.0' / 'abc' / 'claude.exe')
    newest = _touch(base / '1.10.2' / 'def' / 'claude.exe')  # numerically newer than 1.9.0, though it sorts lower as text
    _touch(base / '1.2.0' / 'ghi' / 'claude.exe')
    assert find_cli() == newest


@windows_only
def test_find_cli_searches_the_shorter_layout_and_roaming(no_path_cli, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(no_path_cli / 'local'))
    roaming = no_path_cli / 'roaming' / 'Claude' / 'claude-code'
    _touch(roaming / '2.0.0' / 'claude.exe')
    found = _touch(roaming / '2.1.0' / 'claude.exe')
    assert find_cli() == found


def test_find_cli_returns_a_configured_path(tmp_path):
    exe = _touch(tmp_path / 'my-claude.exe')
    os.chmod(exe, 0o755)
    assert find_cli(exe) == exe


def test_find_cli_missing_configured_path_raises(tmp_path):
    missing = str(tmp_path / 'nope' / 'claude.exe')
    with pytest.raises(LLMError, match='not found'):
        find_cli(missing)


def test_find_cli_nothing_installed_raises(no_path_cli, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(no_path_cli / 'local'))
    with pytest.raises(LLMError, match='local CLI model was not found'):
        find_cli()


PROJECT_VARIABLES = {'STUDIO_TOKEN': 'tok-secret', 'ANTHROPIC_API_KEY': 'sk-secret', 'PYTHONPATH': 'C:/project/server',
                     'GEMINI_API_KEY': 'g-secret', 'STUDIO_PYTHON': 'C:/project/python.exe'}


def test_cli_environment_has_no_project_variables(monkeypatch):
    for key, value in PROJECT_VARIABLES.items():
        monkeypatch.setenv(key, value)
    environment = cli_environment()
    for key, value in PROJECT_VARIABLES.items():
        assert key not in environment
        assert value not in environment.values()
    assert environment['HOME']
    if os.name == 'nt':
        assert set(environment) <= set(claude_cli.WINDOWS_ENV) | {'PATH', 'HOME'}
    else:
        assert set(environment) == {'HOME', 'USER', 'PATH', 'TMPDIR'}


@windows_only
def test_cli_environment_windows_keeps_system_variables_and_a_system_only_path(monkeypatch):
    monkeypatch.setenv('SYSTEMROOT', r'D:\Win')
    monkeypatch.setenv('TEMP', r'D:\Temp')
    monkeypatch.setenv('PATH', r'C:\Project\.venv\Scripts;C:\Tools')
    environment = cli_environment()
    assert environment['SYSTEMROOT'] == r'D:\Win'
    assert environment['TEMP'] == r'D:\Temp'
    assert environment['PATH'].split(os.pathsep) == [r'D:\Win\System32', r'D:\Win', r'D:\Win\System32\Wbem']


def test_cli_environment_posix_path_is_fixed(monkeypatch):
    if os.name == 'nt':
        pytest.skip('Unix branch')
    monkeypatch.setenv('PATH', '/project/venv/bin')
    assert cli_environment()['PATH'] == '/usr/bin:/bin:/usr/sbin:/sbin'


STUB = textwrap.dedent('''
    import json, sys
    argv = sys.argv[1:]
    system_prompt = open(argv[argv.index('--system-prompt-file') + 1], encoding='utf-8').read()
    event = json.loads(sys.stdin.readline())
    blocks = event['message']['content']
    seen = {
        'type': event['type'], 'role': event['message']['role'],
        'text': [b['text'] for b in blocks if b['type'] == 'text'],
        'images': [b['source']['media_type'] for b in blocks if b['type'] == 'image'],
        'system': system_prompt, 'model': argv[argv.index('--model') + 1],
        'stream_json': argv[argv.index('--input-format') + 1] == 'stream-json',
        'env_leak': sorted(k for k in ('STUDIO_TOKEN', 'ANTHROPIC_API_KEY', 'PYTHONPATH', 'GEMINI_API_KEY') if k in __import__('os').environ),
    }
    print('not json, ignored')
    print(json.dumps({'type': 'system', 'subtype': 'init'}))
    print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'thinking'}]}}))
    print(json.dumps({'type': 'result', 'is_error': False, 'result': json.dumps(seen), 'num_turns': 1, 'total_cost_usd': 0.01,
                      'usage': {'input_tokens': 11, 'output_tokens': 7}}))
''')


@pytest.fixture
def stub_cli(monkeypatch, tmp_path):
    """Runs the adapter's real command line against a stub script: the executable becomes this Python
    and the stub script is inserted before the CLI's own arguments (so the real flags are still passed and checked)."""
    script = tmp_path / 'stub_cli.py'
    script.write_text(STUB, encoding='utf-8')
    real = asyncio.create_subprocess_exec

    async def fake_exec(cli, *args, **kwargs):
        assert cli == 'fake-claude'
        return await real(sys.executable, str(script), *args, **kwargs)

    monkeypatch.setattr(claude_cli, 'find_cli', lambda configured='': 'fake-claude')
    monkeypatch.setattr(claude_cli.asyncio, 'create_subprocess_exec', fake_exec)
    for key, value in PROJECT_VARIABLES.items():
        monkeypatch.setenv(key, value)


async def test_stub_cli_stream_json_round_trip(stub_cli):
    import json
    usage = []
    cli = ClaudeCLI(model='claude-sonnet-5-5', on_usage=usage.append)
    out = json.loads(await cli.invoke('Hello there', 'Be brief.'))
    assert out['type'] == 'user' and out['role'] == 'user'
    assert out['text'] == ['Hello there']
    assert out['system'] == 'Be brief.'
    assert out['model'] == 'claude-sonnet-5-5'
    assert out['stream_json'] is True
    assert out['env_leak'] == []
    assert cli.last_call['ok'] is True
    assert cli.last_call['input'] == 11 and cli.last_call['output'] == 7
    assert usage and usage[0]['output'] == 7


async def test_stub_cli_sends_images_as_base64_blocks(stub_cli):
    import json
    image = Image(mime_type='image/png', data_base64=base64.b64encode(b'png').decode(), label='front')
    out = json.loads(await ClaudeCLI().invoke_multimodal('Describe', 'sys', [image]))
    assert out['images'] == ['image/png']
    assert out['text'] == ['Image: front', 'Describe']


async def test_stub_cli_error_result_raises(stub_cli, monkeypatch, tmp_path):
    script = tmp_path / 'stub_cli.py'
    script.write_text('import json,sys\nsys.stdin.readline()\nprint(json.dumps({"type":"result","is_error":True,"result":"Not logged in"}))\n')
    with pytest.raises(LLMError, match='Not logged in'):
        await ClaudeCLI().invoke('hi', 'sys')
