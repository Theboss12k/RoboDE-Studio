"""LLM adapter for the Google Antigravity CLI (`agy`), signed in with the user's own Google account.

Each call is one isolated headless run (https://antigravity.google/docs/cli/headless, /docs/settings, /docs/permissions):

    agy --agent studio --model <id> --input-format stream-json --output-format stream-json
        --settings '<tools-off settings>' --disable-slash-commands [--effort e] [--json-schema s]     (message on stdin)

Behaviour this relies on (documented, and checked by real calls on 2026-11-21, agy 1.5.13):
- `--input-format stream-json` reads one NDJSON message per line from stdin, `{"event": "user", "message": {"content": text}}`,
  runs one turn and exits at end of input; the prompt is never in argv. The output is NDJSON: `init`, `step_update`
  events and a final `result` event `{status: SUCCESS|ERROR, response, error?, structured_output?, num_turns,
  usage: {input_tokens, output_tokens, thinking_tokens, cache_read_tokens, total_tokens}}`. `input_tokens` includes the
  cached part; `output_tokens` includes the thinking tokens.
- The system prompt is the body of a custom agent file, `.agents/agents/studio/agent.md` in the working folder, selected
  with `--agent studio`; it replaces the default coding-agent prompt (a one-line call costs about 2k input tokens, not 9k).
- `agy` is a coding agent with shell, file, browser and MCP tools, so every call is locked down:
  * it runs in a fresh empty temporary folder, so nothing of this machine's projects is in reach;
  * the agent file's `tools:` list names the only tools the model is given (`view_file` as a harmless placeholder, plus
    `finish` when a schema is enforced; `search_web` and `read_url_content` for `with_web()`), so the shell, file edits,
    browser and MCP tools are not offered to the model;
  * `--settings` adds permission deny rules for every permission action (`command`, `read_file`, `write_file`, `mcp`,
    `execute_url`, `download_url`, `read_url`), `toolPermission: strict` and the terminal sandbox; deny rules apply in
    every mode, so a tool call that still slipped through is refused. `with_web()` only allows `read_url(*)`;
  * `--dangerously-skip-permissions` is never used, `--mode` is never set, and slash commands and skills are off.
- `--json-schema` enforces `structured(schema)`: the parsed object is in `structured_output` (the model delivers it
  through the `finish` tool, which is why that tool is listed when a schema is given).

No API key is passed. The child gets a short allow-list of environment variables, so `*_API_KEY` values, STUDIO_TOKEN and
STUDIO_CALLBACK_SECRET are not inherited; the CLI uses its own saved Google sign-in (run `agy` once in a terminal).
The `effort` of a profile is passed as `--effort`. Images are not supported (the stream input takes text blocks only),
so `invoke_multimodal` is deliberately absent. Usage is Antigravity plan quota, not a list price: `cost_usd` is None.
"""
import asyncio
import json
import os
import pwd
import shutil
import signal
import tempfile
import time

from .base import LLMError

MODELS = ('gemini-3.8-flash-high', 'gemini-3.8-flash-medium', 'gemini-3.8-flash-low')
AGENT = 'studio'
WEB_TOOLS = ('search_web', 'read_url_content')
PLACEHOLDER_TOOLS = ('view_file',)   # an agent file needs a tool list; this one is denied by the read_file rule anyway
DENIED = ('command(*)', 'write_file(*)', 'read_file(*)', 'mcp(*)', 'execute_url(*)', 'download_url(*)', 'read_url(*)')
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
MAX_INPUT_BYTES = 8 * 1024 * 1024
KEPT_ENVIRONMENT = ('LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'ALL_PROXY', 'http_proxy',
                    'https_proxy', 'no_proxy', 'all_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS',
                    'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME')


def _home():
    return pwd.getpwuid(os.getuid()).pw_dir


def find_cli(configured=''):
    """The CLI path: the configured one, then PATH, then the installer's folder `~/.gemini/bin` (not on PATH by default)."""
    if configured:
        if os.access(configured, os.X_OK):
            return configured
        raise LLMError(f'Antigravity CLI not found at {configured}')
    found = shutil.which('agy')
    if found:
        return found
    home = _home()
    for path in (os.path.join(home, '.gemini/bin/agy'), os.path.join(home, '.local/bin/agy'), '/opt/homebrew/bin/agy',
                 '/usr/local/bin/agy'):
        if os.access(path, os.X_OK):
            return path
    raise LLMError('Antigravity CLI (agy) not found. Install Google Antigravity, sign in by running "agy" once, '
                   'or set the CLI path in the LLM profile.')


def settings(web):
    """The `--settings` object for one call (session only, never saved). Plain calls may use no tool; web calls may read URLs."""
    deny = [rule for rule in DENIED if not (web and rule == 'read_url(*)')]
    permissions = {'deny': deny, **({'allow': ['read_url(*)']} if web else {})}
    return {'permissions': permissions, 'toolPermission': 'strict', 'agentMode': '', 'enableTerminalSandbox': True,
            'disableSlashCommands': True, 'allowNonWorkspaceAccess': False}


def agent_file(system_prompt, web, structured):
    """The custom agent: its body is the whole system prompt and its `tools:` list is everything the model may call."""
    tools = list(WEB_TOOLS) if web else list(PLACEHOLDER_TOOLS)
    if structured:
        tools.append('finish')
    body = (system_prompt or 'You are a helpful assistant.').replace('\r\n', '\n')
    if web:
        body += '\n\nYou may use the search_web and read_url_content tools to research. You have no other tools.'
    return '---\nname: ' + AGENT + '\ndescription: Studio assistant\ntools:\n' + ''.join(f'  - {t}\n' for t in tools) + '---\n' + body + '\n'


def explain(detail, code):
    """A helpful LLMError message for a CLI failure."""
    text = str(detail or '').strip()
    low = text.lower()
    if any(w in low for w in ('authentication required', 'not logged in', 'login', 'log in', 'sign in', 'unauthenticated', 'credentials')):
        return f'Antigravity CLI is not signed in. Run "agy" once in a terminal and sign in with Google, then try again. ({text[:200]})'
    if any(w in low for w in ('429', 'quota', 'resource_exhausted', 'rate limit', 'rate-limit', 'credits')):
        return f'Antigravity CLI quota or rate limit reached. Wait a while or use another model. {text[:300]}'.strip()
    if 'invalid model selection' in low or 'not recognized as a known model' in low:
        return f'Antigravity CLI could not use this model. Check the model name (run "agy models"). {text[:300]}'.strip()
    return ('Antigravity CLI: ' + (text or f'exit {code}'))[:420]


async def _kill_tree(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)   # the CLI starts child processes; kill the whole group
    except (ProcessLookupError, PermissionError):
        process.kill()
    await process.wait()


def _count(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


class AntigravityCLI:
    kind = 'antigravity_cli'

    def __init__(self, model='gemini-3.8-flash-medium', effort='', timeout_s=240, cli_path='', schema=None, on_usage=None, web=False):
        self.model, self.effort, self.timeout_s, self.cli_path, self.schema = model, effort, timeout_s, cli_path, schema
        self.on_usage = on_usage  # callable(entry) the registry attaches to count tokens
        self.web = web            # allow search_web and read_url_content (research mode only)
        self.last_call = None

    def structured(self, schema):
        """A copy that enforces `schema` with the CLI's --json-schema."""
        return AntigravityCLI(self.model, self.effort, self.timeout_s, self.cli_path, schema, self.on_usage, self.web)

    def with_web(self, timeout_s=900):
        """A copy that may search and read the web. Only search_web and read_url_content are given to the model (no files,
        no shell) and the call still runs in an empty folder."""
        return AntigravityCLI(self.model, self.effort, max(self.timeout_s, timeout_s), self.cli_path, self.schema, self.on_usage, True)

    async def invoke(self, input: str, system_prompt: str) -> str:
        return await self._run(input, system_prompt)

    def _environment(self, cli):
        environment = {name: os.environ[name] for name in KEPT_ENVIRONMENT if os.environ.get(name)}
        folders = [os.path.dirname(os.path.realpath(cli)), os.path.dirname(cli), *os.environ.get('PATH', '').split(os.pathsep),
                   '/usr/bin', '/bin']
        environment.update({'HOME': _home(), 'USER': os.environ.get('USER', ''), 'TMPDIR': os.environ.get('TMPDIR', '/tmp'),
                            'PATH': os.pathsep.join(dict.fromkeys(f for f in folders if f)), 'NO_COLOR': '1'})
        return environment

    def command(self, cli):
        command = [cli, '--agent', AGENT, '--model', self.model, '--input-format', 'stream-json', '--output-format', 'stream-json',
                   '--settings', json.dumps(settings(self.web)), '--disable-slash-commands']
        if self.effort in EFFORTS:
            command += ['--effort', self.effort]
        if self.schema is not None:
            command += ['--json-schema', json.dumps(self.schema)]
        return command

    async def _run(self, text, system_prompt):
        cli = find_cli(self.cli_path)
        message = (json.dumps({'event': 'user', 'message': {'content': text}}) + '\n').encode()
        if len(message) > MAX_INPUT_BYTES:
            raise LLMError(f'Antigravity CLI input is too large ({len(message) // 1024} KiB; the limit is {MAX_INPUT_BYTES // 1024} KiB)')
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix='studio-agy-') as empty:
            folder = os.path.join(empty, '.agents', 'agents', AGENT)
            os.makedirs(folder)
            with open(os.path.join(folder, 'agent.md'), 'w') as f:
                f.write(agent_file(system_prompt, self.web, self.schema is not None))
            process = await asyncio.create_subprocess_exec(
                *self.command(cli), cwd=empty, env=self._environment(cli), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024, start_new_session=True)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(message), self.timeout_s)
            except asyncio.TimeoutError:
                await _kill_tree(process)
                raise LLMError(f'Antigravity CLI timed out after {self.timeout_s} s') from None
            except asyncio.CancelledError:
                await _kill_tree(process)
                raise
        result, web_calls, pages = None, 0, []
        for line in stdout.decode(errors='replace').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get('event') == 'result' and isinstance(event.get('result'), dict):
                result = event['result']
            elif event.get('event') == 'step_update' and isinstance(event.get('step_update'), dict):
                step = event['step_update']
                if step.get('step_type') == 'tool' and step.get('state') == 'DONE' and step.get('tool_name') in WEB_TOOLS:
                    web_calls += 1
                    info = step.get('tool_info') if isinstance(step.get('tool_info'), dict) else {}
                    url = (info.get('parameters') or {}).get('Url') if isinstance(info.get('parameters'), dict) else None
                    if step['tool_name'] == 'read_url_content' and isinstance(url, str):
                        pages.append(url)
        usage = (result or {}).get('usage') if isinstance((result or {}).get('usage'), dict) else {}
        cached = _count(usage.get('cache_read_tokens'))
        ok = bool(result) and result.get('status') == 'SUCCESS' and not result.get('error')
        self.last_call = {'model': self.model, 'seconds': round(time.monotonic() - started, 2), 'images': 0,
                          'input': max(0, _count(usage.get('input_tokens')) - cached), 'cache_write': 0, 'cache_read': cached,
                          'output': _count(usage.get('output_tokens')), 'thinking': _count(usage.get('thinking_tokens')),
                          'cost_usd': None, 'turns': (result or {}).get('num_turns'), 'structured': self.schema is not None,
                          'ok': ok, 'web_calls': web_calls, 'pages_opened': pages[:40]}
        if self.on_usage and result is not None:
            try:
                self.on_usage(self.last_call)
            except Exception:  # counting must never break a call
                pass
        if result is None:
            raise LLMError(explain(stderr.decode(errors='replace') or stdout.decode(errors='replace')[-400:], process.returncode))
        if not ok:
            raise LLMError(explain(result.get('error') or result.get('status') or stderr.decode(errors='replace'), process.returncode))
        if self.schema is not None and result.get('structured_output') is not None:
            return json.dumps(result['structured_output'])
        response = result.get('response')
        if not isinstance(response, str) or not response.strip():
            raise LLMError('Antigravity CLI returned no text')
        return response.strip()
