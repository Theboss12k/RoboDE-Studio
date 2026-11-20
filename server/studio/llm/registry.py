"""Saved LLM profiles and which profile each role uses.

Roles:
- spec:     turns the request into a technical description and asks questions
- scene:    builds the 3D scene
- robot:    drafts a rough robot concept
- revise:   applies a user's change request
- search:   finds real robots and prices on the web
- extract:  reads a manufacturer datasheet
- analysis: explains differences
- objects:  makes simple 3D models of the task objects (a small model)
- checks:   plans task-specific checks (formulas over named quantities); code evaluates them
- ask:      answers the Ask bar's questions and proposes actions (it never performs them; Java does)

Providers are plugged in through PROVIDERS (kind -> factory(profile)). Adding a provider means adding one adapter
module with `invoke(input, system_prompt)` and one entry here; nothing else in the studio changes.
- claude_cli: Claude through the Claude Code CLI and its own sign-in (no key).
- gemini:     Google Gemini through the Gemini API; its key comes from llm/keys.py, never from the profile.
"""
import uuid

from pydantic import BaseModel, Field

from ..util import atomic_json, read_json
from . import gemini as gemini_module
from .claude_cli import MODELS, ClaudeCLI
from .keys import KeyStore
from .usage import UsageLedger

ROLES = ('spec', 'scene', 'robot', 'revise', 'search', 'extract', 'analysis', 'objects', 'ask', 'checks')
DEFAULT_ROLES = {'spec': 'claude_opus', 'scene': 'claude_sonnet', 'robot': 'claude_opus', 'revise': 'claude_opus', 'search': 'claude_sonnet', 'extract': 'claude_sonnet', 'analysis': 'claude_sonnet', 'objects': 'claude_haiku', 'ask': 'claude_sonnet', 'checks': 'claude_opus'}


def claude_cli(profile):
    return ClaudeCLI(model=profile['model'], effort=profile.get('effort', ''), timeout_s=profile.get('timeout_s', 240),
                     cli_path=profile.get('cli_path', ''))


def gemini(profile):
    return gemini_module.GeminiAPI(model=profile['model'], effort=profile.get('effort', ''),
                                   timeout_s=profile.get('timeout_s', 240), api_key=profile.get('api_key', ''))


PROVIDERS = {'claude_cli': claude_cli, 'gemini': gemini}
KEYED = {'gemini'}  # providers whose factory receives the saved API key as profile['api_key']


class Profile(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(default='claude_cli', pattern=r'^[a-z_]+$')
    model: str = Field(default='claude-sonnet-5-5', max_length=120)
    effort: str = Field(default='', pattern=r'^(|low|medium|high|xhigh|max)$')
    timeout_s: int = Field(default=240, ge=10, le=900)
    cli_path: str = Field(default='', max_length=1000)


DEFAULTS = [
    {'id': 'claude_opus', 'name': 'Claude Opus 5.5 (Claude Code CLI)', 'kind': 'claude_cli', 'model': 'claude-opus-5-5'},
    {'id': 'claude_sonnet', 'name': 'Claude Sonnet 5.5 (Claude Code CLI)', 'kind': 'claude_cli', 'model': 'claude-sonnet-5-5'},
    {'id': 'claude_haiku', 'name': 'Claude Haiku 5.5 (Claude Code CLI)', 'kind': 'claude_cli', 'model': 'claude-haiku-5-5'},
]


def default_profiles():
    return [dict(p, effort='', timeout_s=240, cli_path='') for p in DEFAULTS]


class LLMRegistry:
    def __init__(self, runtime, overrides=None):
        self.path = runtime / 'llm' / 'profiles.json'
        self.overrides = overrides or {}  # role or profile id -> adapter object (tests)
        self.usage = UsageLedger(runtime)
        self.keys = KeyStore(runtime)

    def data(self):
        data = read_json(self.path)
        if data is None:
            data = {'profiles': default_profiles(),
                    'roles': dict(DEFAULT_ROLES)}
        data['roles'] = {**DEFAULT_ROLES, **data['roles']}  # roles added later get their default
        have = {p['id'] for p in data['profiles']}
        for default in default_profiles():  # a default profile a role points to (a role added later) is added when missing
            if default['id'] not in have and default['id'] in data['roles'].values():
                data['profiles'] = [*data['profiles'], default]
        return data

    def save(self, data):
        atomic_json(self.path, data)

    def describe(self):
        return {**self.data(), 'kinds': sorted(PROVIDERS), 'roles_available': list(ROLES), 'keys': self.keys.status(),
                'models': {'claude_cli': list(MODELS), 'gemini': list(gemini_module.MODELS)}}

    def upsert(self, profile: Profile, profile_id=None):
        if profile.kind not in PROVIDERS:
            raise ValueError(f'Unknown provider kind {profile.kind}')
        if profile.kind == 'gemini' and not profile.model.startswith('gemini'):
            raise ValueError('A Gemini profile needs a Gemini model name, such as gemini-3.8-flash')
        data = self.data()
        profile_id = profile_id or uuid.uuid4().hex[:10]
        entry = {'id': profile_id, **profile.model_dump()}
        data['profiles'] = [p for p in data['profiles'] if p['id'] != profile_id] + [entry]
        self.save(data)
        return entry

    def delete(self, profile_id):
        data = self.data()
        if profile_id in data['roles'].values():
            raise ValueError('This profile is assigned to a role; assign another profile first')
        data['profiles'] = [p for p in data['profiles'] if p['id'] != profile_id]
        self.save(data)

    def set_roles(self, roles):
        data = self.data()
        ids = {p['id'] for p in data['profiles']}
        for role, profile_id in roles.items():
            if role not in ROLES or profile_id not in ids:
                raise ValueError(f'Unknown role or profile: {role} -> {profile_id}')
            data['roles'][role] = profile_id
        self.save(data)
        return data['roles']

    def profile(self, profile_id):
        for p in self.data()['profiles']:
            if p['id'] == profile_id:
                return p
        raise KeyError(f'LLM profile {profile_id} not found')

    def adapter(self, profile_id, role=None):
        if profile_id in self.overrides:
            return self.overrides[profile_id]
        profile = self.profile(profile_id)
        if profile['kind'] not in PROVIDERS:
            raise ValueError(f'LLM profile {profile_id} uses an unknown provider {profile["kind"]}')
        if profile['kind'] in KEYED:
            profile = {**profile, 'api_key': self.keys.get(profile['kind'])}
        adapter = PROVIDERS[profile['kind']](profile)
        if hasattr(adapter, 'on_usage'):  # providers that report tokens get counted
            adapter.on_usage = lambda entry: self.usage.record(role or 'test', profile_id, entry)
        return adapter

    def for_role(self, role):
        if role in self.overrides:
            return self.overrides[role]
        return self.adapter(self.data()['roles'][role], role)
