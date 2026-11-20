"""`TaskLLMs`: LLM adapters built from the resolved profile dicts of a request (no registry, no files, no env keys).

Providers come from `llm.registry.PROVIDERS`, so provider modules stay independent of tasks and editions. A keyed
provider gets the key from the profile dict (`api_key`), which Java filled in; keys are never read from env or argv here.
The `scripted` kind (fixture rules) is for tests only and needs STUDIO_ALLOW_SCRIPTED=1 in the process env.
"""
import asyncio
import json
import os
from pathlib import Path

from ..llm.registry import PROVIDERS, ROLES
from ..llm.scripted import ScriptedLLM


class FixtureLLM(ScriptedLLM):
    """Scripted adapter driven by a fixture file {"rules": [{"match", "answers": [...]}]}. Within a rule the answers are
    consumed in order and the last one repeats. Every call is appended to the call log (llm_calls.jsonl)."""

    def __init__(self, rules, role, log_path):
        super().__init__([])
        self.fixture_rules = rules          # [(match, answers list, delay_s)], shared and consumed in place
        self.role, self.log_path = role, log_path
        self.on_usage = None                # set by TaskLLMs: each answered call reports a small fixture usage entry

    async def _delay(self, input, system_prompt):
        """Sleep the matching rule's optional delay_s (fixture adapter only; lets tests hold a job open)."""
        for match, _answers, delay in self.fixture_rules:
            if match in system_prompt or match in input:
                if delay:
                    await asyncio.sleep(delay)
                return

    async def invoke(self, input: str, system_prompt: str) -> str:
        await self._delay(input, system_prompt)
        return self._answer(input, system_prompt, [])

    async def invoke_multimodal(self, input: str, system_prompt: str, images) -> str:
        await self._delay(input, system_prompt)
        return self._answer(input, system_prompt, images)

    def _answer(self, input, system_prompt, images):
        self.calls.append({'input': input, 'system_prompt': system_prompt, 'images': len(images)})
        for match, answers, _delay in self.fixture_rules:
            if match in system_prompt or match in input:
                self._log(match, system_prompt, input, len(images))
                answer = answers.pop(0) if len(answers) > 1 else answers[0]
                if self.on_usage:
                    self.on_usage({'model': 'fixture', 'seconds': 0.0, 'images': len(images), 'input': 1, 'cache_write': 0,
                                   'cache_read': 0, 'output': 1, 'cost_usd': 0.0, 'turns': 1, 'structured': True, 'ok': True,
                                   'web_calls': 0})
                return answer if isinstance(answer, str) else json.dumps(answer)
        self._log(None, system_prompt, input, len(images))
        raise AssertionError('No scripted answer for this prompt: ' + system_prompt[:120])

    def _log(self, match, system_prompt, input, images=0):
        if self.log_path:
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps({'role': self.role, 'match': match, 'system_prompt': system_prompt, 'input': input,
                                    'images': images}) + '\n')


class TaskLLMs:
    def __init__(self, roles, on_usage=None, out_dir=None):
        """roles: {role: profile dict}. on_usage(role, profile_id, entry) receives each call's usage entry."""
        self.roles = dict(roles or {})
        self.on_usage = on_usage
        self.log_path = str(Path(out_dir) / 'llm_calls.jsonl') if out_dir else None
        self._cache = {}
        self._fixtures = {}       # fixture path -> rules, so answers are consumed across roles that share a file

    def secrets(self):
        return [p.get('api_key') for p in self.roles.values() if isinstance(p, dict) and p.get('api_key')]

    def for_role(self, role):
        if role not in self._cache:
            if role not in ROLES:
                raise ValueError(f'Unknown LLM role {role}')
            profile = self.roles.get(role)
            if not isinstance(profile, dict):
                raise ValueError(f'No LLM profile was given for the {role} role')
            self._cache[role] = self.build(profile, role)
        return self._cache[role]

    def build(self, profile, role):
        kind = profile.get('kind')
        if kind == 'scripted':
            if os.environ.get('STUDIO_ALLOW_SCRIPTED') != '1':
                raise ValueError('Scripted LLM profiles are only allowed in tests (STUDIO_ALLOW_SCRIPTED=1)')
            path = str(profile['fixture'])
            if path not in self._fixtures:
                rules = json.loads(Path(path).read_text(encoding='utf-8'))['rules']
                self._fixtures[path] = [(r['match'], list(r.get('answers') or [r.get('answer', {})]),
                                        min(max(float(r.get('delay_s') or 0), 0.0), 30.0)) for r in rules]
            llm = FixtureLLM(self._fixtures[path], role, self.log_path)
            if self.on_usage:
                llm.on_usage = lambda entry: self.on_usage(role, profile.get('id') or kind, entry)
            return llm
        if kind not in PROVIDERS:
            raise ValueError(f'LLM profile {profile.get("id", "")} uses an unknown provider {kind}')
        adapter = PROVIDERS[kind](profile)
        if hasattr(adapter, 'on_usage') and self.on_usage:
            adapter.on_usage = lambda entry: self.on_usage(role, profile.get('id') or kind, entry)
        return adapter
