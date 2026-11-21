"""LLM adapter for the Claude Code CLI (`claude -p`).

Each call is one isolated CLI run:
- the caller's system prompt replaces Claude Code's own;
- tools, MCP servers, slash commands, settings and session history are off;
- it runs in an empty temporary folder with a reduced environment, so no project files or instructions are read.

Text and images go in as one stream-json user message; images are base64 content blocks. When a JSON
schema is given (`structured(schema)`), the CLI's `--json-schema` enforces it and the parsed object is returned
as JSON text. The CLI uses its own login; no API key passes through RoboDE.
"""
import asyncio
import glob
import json
import os
import shutil
import tempfile
import time

try:
    import pwd
except ImportError:  # Windows: no pwd module
    pwd = None

from .base import Image, LLMError

MODELS = ('claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-fable-5-1')
MAX_IMAGES = 8


def _home():
    """The user's real home directory: from the password database on Unix (ignores a changed HOME), else ~."""
    return pwd.getpwuid(os.getuid()).pw_dir if pwd else os.path.expanduser('~')


def find_cli(configured=''):
    """The CLI path: the configured one, then PATH, then the Claude desktop app's bundled copies (newest first)."""
    if configured:
        if os.access(configured, os.X_OK):
            return configured
        raise LLMError(f'The local CLI model was not found at {configured}')
    found = shutil.which('claude')
    if found:
        return found
    home = _home()
    if os.name == 'nt':
        local, roaming = os.environ.get('LOCALAPPDATA', ''), os.environ.get('APPDATA', '')
        patterns = [os.path.join(home, '.local', 'bin', 'claude.exe'), os.path.join(home, '.claude', 'local', 'claude.exe'),
                    os.path.join(local, 'Claude-Data', '*', 'claude-code', '*', '*', 'claude.exe'),
                    os.path.join(local, 'Claude-Data', '*', 'claude-code', '*', 'claude.exe'),
                    os.path.join(roaming, 'Claude', 'claude-code', '*', '*', 'claude.exe'),
                    os.path.join(roaming, 'Claude', 'claude-code', '*', 'claude.exe')]
    else:
        patterns = [os.path.join(home, 'Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude'),
                    os.path.join(home, 'Library/Application Support/Claude/claude-code/*/claude'),
                    os.path.join(home, '.claude/local/claude'), os.path.join(home, '.local/bin/claude')]
    candidates = [p for pattern in patterns for p in glob.glob(pattern) if os.path.isfile(p) and os.access(p, os.X_OK)]

    def version(path):
        parts = [part for part in path.split(os.sep) if part[:1].isdigit() and '.' in part]
        return tuple(int(n) for n in parts[0].split('.') if n.isdigit()) if parts else ()
    if candidates:
        return max(candidates, key=version)
    raise LLMError('The local CLI model was not found. Install its command-line tool or set the CLI path in the model profile.')


WINDOWS_ENV = ('SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'PATHEXT', 'USERPROFILE', 'USERNAME', 'USERDOMAIN',
               'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'PROGRAMFILES', 'TEMP', 'TMP',
               'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE')


def cli_environment():
    """The reduced environment the CLI runs with: enough to find its login and start, nothing from the project.
    Windows programs need SYSTEMROOT, the profile folders and TEMP to start at all, so those are kept there."""
    home = _home()
    if os.name == 'nt':
        environment = {key: os.environ[key] for key in WINDOWS_ENV if key in os.environ}
        root = environment.get('SYSTEMROOT', r'C:\Windows')
        environment['PATH'] = os.pathsep.join([os.path.join(root, 'System32'), root, os.path.join(root, 'System32', 'Wbem')])
        environment['HOME'] = home
        return environment
    return {'HOME': home, 'USER': os.environ.get('USER', ''), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
            'TMPDIR': os.environ.get('TMPDIR', '/tmp')}


class ClaudeCLI:
    kind = 'claude_cli'

    def __init__(self, model='claude-sonnet-5-5', effort='', timeout_s=240, cli_path='', schema=None, on_usage=None, web=False):
        self.model, self.effort, self.timeout_s, self.cli_path, self.schema = model, effort, timeout_s, cli_path, schema
        self.on_usage = on_usage  # callable(entry) the registry attaches to count tokens
        self.web = web            # allow the CLI's WebSearch and WebFetch tools (research mode only)
        self.last_call = None

    def structured(self, schema):
        """A copy that enforces `schema` with the CLI's --json-schema (costs one extra turn)."""
        return ClaudeCLI(self.model, self.effort, self.timeout_s, self.cli_path, schema, self.on_usage, self.web)

    def with_web(self, timeout_s=900):
        """A copy that may search and read the web. Only WebSearch and WebFetch are enabled (no files, no shell),
        and the call still runs in an empty folder, so web pages can't reach anything on this machine."""
        return ClaudeCLI(self.model, self.effort, max(self.timeout_s, timeout_s), self.cli_path, self.schema, self.on_usage, True)

    async def invoke(self, input: str, system_prompt: str) -> str:
        return await self._run(input, system_prompt, [])

    async def invoke_multimodal(self, input: str, system_prompt: str, images: list[Image]) -> str:
        return await self._run(input, system_prompt, images[:MAX_IMAGES])

    async def _run(self, text, system_prompt, images):
        cli = find_cli(self.cli_path)
        environment = cli_environment()
        content = []
        for image in images:
            if image.label:
                content.append({'type': 'text', 'text': f'Image: {image.label}'})
            content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': image.mime_type, 'data': image.data_base64}})
        content.append({'type': 'text', 'text': text})
        message = json.dumps({'type': 'user', 'message': {'role': 'user', 'content': content}}) + '\n'
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix='studio-claude-') as empty:
            system_file = os.path.join(empty, '.system-prompt')
            with open(system_file, 'w', encoding='utf-8') as f:
                f.write(system_prompt or 'You are a helpful assistant.')
            tools = ['--tools', 'WebSearch,WebFetch', '--allowedTools', 'WebSearch,WebFetch'] if self.web else ['--tools', '']
            command = [cli, '-p', '--system-prompt-file', system_file, *tools, '--input-format', 'stream-json',
                       '--output-format', 'stream-json', '--verbose', '--model', self.model, '--no-session-persistence',
                       '--strict-mcp-config', '--disable-slash-commands', '--setting-sources', '',
                       *(['--effort', self.effort] if self.effort else []),
                       *(['--json-schema', json.dumps(self.schema)] if self.schema is not None else [])]
            process = await asyncio.create_subprocess_exec(*command, cwd=empty, env=environment, stdin=asyncio.subprocess.PIPE,
                                                           stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                           limit=64 * 1024 * 1024)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(message.encode()), self.timeout_s)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise LLMError(f'The local CLI model timed out after {self.timeout_s} s')
        result, web_calls, sources = None, 0, []
        for line in stdout.decode(errors='replace').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') == 'result':
                result = event
            elif event.get('type') == 'assistant':
                for block in event.get('message', {}).get('content', []):
                    if block.get('type') == 'tool_use' and block.get('name') in ('WebSearch', 'WebFetch'):
                        web_calls += 1
                        if block['name'] == 'WebFetch' and block.get('input', {}).get('url'):
                            sources.append(block['input']['url'])
        usage = (result or {}).get('usage') or {}
        self.last_call = {'model': self.model, 'seconds': round(time.monotonic() - started, 2), 'images': len(images),
                          'input': usage.get('input_tokens', 0), 'cache_write': usage.get('cache_creation_input_tokens', 0),
                          'cache_read': usage.get('cache_read_input_tokens', 0), 'output': usage.get('output_tokens', 0),
                          'cost_usd': (result or {}).get('total_cost_usd'), 'turns': (result or {}).get('num_turns'),
                          'structured': self.schema is not None, 'ok': bool(result) and not result.get('is_error'),
                          'web_calls': web_calls, 'pages_opened': sources[:40]}
        if self.on_usage and result is not None:
            try:
                self.on_usage(self.last_call)
            except Exception:  # counting must never break a call
                pass
        if result is None or result.get('is_error'):
            detail = (result or {}).get('result') or stderr.decode(errors='replace') or f'exit {process.returncode}'
            raise LLMError('The local CLI model: ' + str(detail).strip()[:400])
        if self.schema is not None and result.get('structured_output') is not None:
            return json.dumps(result['structured_output'])
        if not isinstance(result.get('result'), str):
            raise LLMError('The local CLI model returned no text')
        return result['result']
