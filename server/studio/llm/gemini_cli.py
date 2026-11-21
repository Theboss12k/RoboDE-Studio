"""LLM adapter for the Gemini CLI (`gemini`), signed in with the user's own Google account.

Each call is one isolated headless run (https://github.com/google-gemini/gemini-cli, docs/cli/headless.md):

    gemini -m <model> -o json --approval-mode default --allowed-mcp-server-names none -e none     (prompt on stdin)

Documented behaviour this relies on:
- headless mode when stdin is piped (`cat file | gemini`); the whole prompt goes on stdin, never in argv;
- `-o json` prints one object `{session_id?, response, stats, error?}`; `stats.models.<model>.tokens` holds
  `input` (uncached), `prompt`, `candidates`, `cached`, `thoughts`, `tool`, `total`; `stats.tools.byName.<tool>.count`;
- the system prompt is replaced completely by the file named in GEMINI_SYSTEM_MD (docs/cli/system-prompt.md);
- exit codes: 0 ok, 1 general or API failure, 42 input error, 53 turn limit.

The CLI is an agent with shell and file tools, so every call is locked down (docs/reference/configuration.md, tools.*;
docs/reference/policy-engine.md: in non-interactive mode a tool that needs confirmation is denied):
- it runs in a fresh empty temporary folder, so nothing of this machine's projects is in reach;
- a settings file written there disables every built-in tool: `tools.core` lists no real tool, `tools.exclude` names
  every built-in tool, MCP servers, skills, hooks, agents and context files are off. The same file is also given as
  the system settings file (GEMINI_CLI_SYSTEM_SETTINGS_PATH), which outranks the user's own ~/.gemini/settings.json;
- the approval mode stays `default` (never `auto_edit` or `yolo`);
- `with_web()` leaves only `google_web_search` and `web_fetch`: listed in `tools.core` and `tools.allowed` (so they
  run without a confirmation prompt); the shell and the file tools stay excluded.

No API key is ever passed. The child gets a short allow-list of environment variables, so GEMINI_API_KEY,
GOOGLE_API_KEY and GOOGLE_GENAI_USE_* are not inherited and the CLI uses its own saved Google sign-in
(run `gemini` once in a terminal and choose "Sign in with Google"). Only the temp folder is marked as trusted
(GEMINI_CLI_TRUST_WORKSPACE), so the settings written there apply.

Images are not supported: the CLI reads images through its file tools (`@path`), which are off here, so
`invoke_multimodal` is deliberately absent and roles that need images must use a Gemini API profile.
There is no `structured()`; ask_json puts the schema in the prompt.
"""
import asyncio
import glob
import json
import os
import pwd
import shutil
import signal
import tempfile
import time

from .base import LLMError
from .gemini import estimate_cost

WEB_TOOLS = ('google_web_search', 'web_fetch')
# Every built-in tool name in the CLI's tools reference (docs/reference/tools.md), excluded unless web research needs it.
BUILT_IN_TOOLS = ('run_shell_command', 'glob', 'grep_search', 'search_file_content', 'list_directory', 'read_file',
                  'read_many_files', 'write_file', 'replace', 'ask_user', 'write_todos', 'activate_skill',
                  'get_internal_docs', 'enter_plan_mode', 'exit_plan_mode', 'tracker_create_task', 'tracker_update_task',
                  'tracker_get_task', 'tracker_list_tasks', 'tracker_add_dependency', 'tracker_visualize', 'update_topic',
                  'list_mcp_resources', 'read_mcp_resource', 'save_memory', 'google_web_search', 'web_fetch')
NO_SUCH = '__none__'   # a name that matches nothing, for allow-lists that must stay empty
KEPT_ENVIRONMENT = ('LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'ALL_PROXY', 'http_proxy',
                    'https_proxy', 'no_proxy', 'all_proxy', 'NODE_EXTRA_CA_CERTS', 'SSL_CERT_FILE', 'GOOGLE_CLOUD_PROJECT',
                    'GOOGLE_CLOUD_PROJECT_ID', 'GEMINI_CLI_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME')


def _home():
    return pwd.getpwuid(os.getuid()).pw_dir


def find_cli(configured=''):
    """The CLI path: the configured one, then PATH, then the usual npm and Homebrew install folders."""
    if configured:
        if os.access(configured, os.X_OK):
            return configured
        raise LLMError(f'Gemini CLI not found at {configured}')
    found = shutil.which('gemini')
    if found:
        return found
    home = _home()
    patterns = ['/opt/homebrew/bin/gemini', '/usr/local/bin/gemini', os.path.join(home, '.npm-global/bin/gemini'),
                os.path.join(home, '.local/bin/gemini'), os.path.join(home, '.volta/bin/gemini'),
                os.path.join(home, '.nvm/versions/node/*/bin/gemini')]
    candidates = sorted(p for pattern in patterns for p in glob.glob(pattern) if os.access(p, os.X_OK))
    if candidates:
        return candidates[-1]
    raise LLMError('Gemini CLI not found. Install it with "npm install -g @google/gemini-cli", sign in by running '
                   '"gemini" once, or set the CLI path in the LLM profile.')


def settings(web):
    """The settings document written for one call. Plain calls get no tool at all."""
    allowed = list(WEB_TOOLS) if web else []
    return {
        'tools': {'core': allowed or [NO_SUCH], 'allowed': allowed,
                  'exclude': [name for name in BUILT_IN_TOOLS if name not in allowed]},
        'mcp': {'allowed': [NO_SUCH]},
        'skills': {'enabled': False},
        'hooksConfig': {'enabled': False},
        'experimental': {'enableAgents': False},
        'context': {'fileName': ['STUDIO_NO_CONTEXT_FILE.md']},
        'general': {'checkpointing': {'enabled': False}, 'enableAutoUpdate': False, 'enableAutoUpdateNotification': False},
    }


def _json_object(text):
    """The CLI's JSON object from stdout: the whole text, or the last line-start `{` that parses (warnings may precede it)."""
    text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except ValueError:
        pass
    starts = [i for i in range(len(text)) if text[i] == '{' and (i == 0 or text[i - 1] == '\n')]
    for start in starts:
        try:
            data = json.loads(text[start:])
            if isinstance(data, dict):
                return data
        except ValueError:
            continue
    return None


def _count(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def usage_of(stats, fallback_model):
    """(input, cache_read, output, cost_usd, web_calls) from the CLI's `stats`. Defensive: any field may be missing.
    `input` is uncached prompt tokens plus tool-use tokens; output includes thinking tokens, like the Gemini API adapter."""
    stats = stats if isinstance(stats, dict) else {}
    models = stats.get('models') if isinstance(stats.get('models'), dict) else {}
    uncached = cached = output = 0
    cost, priced = 0.0, bool(models)
    for name, metrics in models.items():
        tokens = (metrics or {}).get('tokens') if isinstance(metrics, dict) else None
        tokens = tokens if isinstance(tokens, dict) else {}
        hit = _count(tokens.get('cached'))
        fresh = _count(tokens.get('input')) if 'input' in tokens else max(0, _count(tokens.get('prompt')) - hit)
        fresh += _count(tokens.get('tool'))
        out = _count(tokens.get('candidates')) + _count(tokens.get('thoughts'))
        uncached, cached, output = uncached + fresh, cached + hit, output + out
        price = estimate_cost(name, fresh, hit, out)
        if price is None:
            if fresh or hit or out:
                priced = False
        else:
            cost += price
    tools = stats.get('tools') if isinstance(stats.get('tools'), dict) else {}
    by_name = tools.get('byName') if isinstance(tools.get('byName'), dict) else {}
    web = sum(_count((by_name.get(tool) or {}).get('count')) for tool in WEB_TOOLS if isinstance(by_name.get(tool), dict))
    return uncached, cached, output, (round(cost, 6) if priced and (uncached or cached or output) else None), web


def explain(detail, code):
    """A helpful LLMError message for a CLI failure."""
    text = str(detail or '').strip()
    low = text.lower()
    if any(w in low for w in ('not logged in', 'login', 'log in', 'sign in', 'authenticat', 'credentials', 'oauth', 'unauthenticated')):
        return ('Gemini CLI is not signed in. Run "gemini" once in a terminal and choose "Sign in with Google", '
                f'then try again. ({text[:200]})')
    if any(w in low for w in ('429', 'quota', 'resource_exhausted', 'rate limit', 'rate-limit')):
        return f'Gemini CLI rate limit or quota reached. Wait a while or use another model. {text[:300]}'.strip()
    if '404' in low or 'not found' in low and 'model' in low:
        return f'Gemini CLI could not use this model. Check the model name. {text[:300]}'.strip()
    if code == 53:
        return 'Gemini CLI stopped: turn limit exceeded'
    if code == 42:
        return f'Gemini CLI rejected the input (exit 42). {text[:300]}'.strip()
    if 'env: node' in low or 'node: command not found' in low:
        return 'Gemini CLI needs Node.js on the PATH of the studio process.'
    return ('Gemini CLI: ' + (text or f'exit {code}'))[:420]


async def _kill_tree(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)   # the CLI starts child processes; kill the whole group
    except (ProcessLookupError, PermissionError):
        process.kill()
    await process.wait()


class GeminiCLI:
    kind = 'gemini_cli'

    def __init__(self, model='gemini-3.8-flash', effort='', timeout_s=240, cli_path='', on_usage=None, web=False):
        self.model, self.effort, self.timeout_s, self.cli_path = model, effort, timeout_s, cli_path  # effort: no CLI flag
        self.on_usage = on_usage  # callable(entry) the registry attaches to count tokens
        self.web = web            # allow google_web_search and web_fetch (research mode only)
        self.last_call = None

    def with_web(self, timeout_s=900):
        """A copy that may search and read the web. Only google_web_search and web_fetch are enabled (no files, no
        shell) and the call still runs in an empty folder."""
        return GeminiCLI(self.model, self.effort, max(self.timeout_s, timeout_s), self.cli_path, self.on_usage, True)

    async def invoke(self, input: str, system_prompt: str) -> str:
        return await self._run(input, system_prompt)

    def _environment(self, cli, empty, system_file, settings_file):
        environment = {name: os.environ[name] for name in KEPT_ENVIRONMENT if os.environ.get(name)}
        folders = [os.path.dirname(os.path.realpath(cli)), os.path.dirname(cli), *os.environ.get('PATH', '').split(os.pathsep),
                   '/usr/bin', '/bin']
        environment.update({'HOME': _home(), 'USER': os.environ.get('USER', ''), 'TMPDIR': os.environ.get('TMPDIR', '/tmp'),
                            'PATH': os.pathsep.join(dict.fromkeys(f for f in folders if f)), 'NO_COLOR': '1',
                            'GEMINI_SYSTEM_MD': system_file, 'GEMINI_CLI_SYSTEM_SETTINGS_PATH': settings_file,
                            'GEMINI_CLI_TRUST_WORKSPACE': 'true'})
        return environment

    async def _run(self, text, system_prompt):
        cli = find_cli(self.cli_path)
        started = time.monotonic()
        system = (system_prompt or 'You are a helpful assistant.')
        if self.web:
            system += '\n\nYou may use the google_web_search and web_fetch tools to research. You have no other tools.'
        with tempfile.TemporaryDirectory(prefix='studio-gemini-') as empty:
            system_file = os.path.join(empty, 'system-prompt.md')
            with open(system_file, 'w') as f:
                f.write(system)
            os.makedirs(os.path.join(empty, '.gemini'))
            settings_file = os.path.join(empty, '.gemini', 'settings.json')
            with open(settings_file, 'w') as f:
                json.dump(settings(self.web), f)
            command = [cli, '-m', self.model, '-o', 'json', '--approval-mode', 'default',
                       '--allowed-mcp-server-names', NO_SUCH, '-e', 'none']
            process = await asyncio.create_subprocess_exec(
                *command, cwd=empty, env=self._environment(cli, empty, system_file, settings_file),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=64 * 1024 * 1024, start_new_session=True)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(text.encode()), self.timeout_s)
            except asyncio.TimeoutError:
                await _kill_tree(process)
                raise LLMError(f'Gemini CLI timed out after {self.timeout_s} s') from None
            except asyncio.CancelledError:
                await _kill_tree(process)
                raise
        data = _json_object(stdout.decode(errors='replace'))
        error = data.get('error') if isinstance(data, dict) else None
        response = data.get('response') if isinstance(data, dict) else None
        uncached, cached, output, cost, web_calls = usage_of((data or {}).get('stats'), self.model)
        ok = isinstance(response, str) and bool(response.strip()) and not error and process.returncode == 0
        self.last_call = {'model': self.model, 'seconds': round(time.monotonic() - started, 2), 'images': 0,
                          'input': uncached, 'cache_write': 0, 'cache_read': cached, 'output': output,
                          'cost_usd': cost, 'turns': 1, 'structured': False, 'ok': ok,
                          'web_calls': web_calls, 'pages_opened': []}
        if self.on_usage and data is not None:
            try:
                self.on_usage(self.last_call)
            except Exception:  # counting must never break a call
                pass
        if error:
            detail = error.get('message') if isinstance(error, dict) else error
            raise LLMError(explain(detail, process.returncode))
        if process.returncode != 0 or data is None:
            raise LLMError(explain(stderr.decode(errors='replace') or stdout.decode(errors='replace')[-400:], process.returncode))
        if not isinstance(response, str) or not response.strip():
            raise LLMError('Gemini CLI returned no text')
        return response
