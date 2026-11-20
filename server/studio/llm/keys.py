"""API keys for providers that need one (Gemini), kept apart from the LLM profiles.

Keys live in runtime/llm/keys.json, readable only by the current user (mode 0600), and runtime/ is git-ignored.
They are never returned by the API, written to the usage ledger or put in profiles.json, so profiles can be
shared or committed without them. An environment variable takes over when no key is saved.
"""
import json
import os

ENVIRONMENT = {'gemini': ('GEMINI_API_KEY', 'GOOGLE_API_KEY')}


class KeyStore:
    def __init__(self, runtime):
        self.path = runtime / 'llm' / 'keys.json'

    def _read(self):
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as f:
            json.dump(data, f)
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    def get(self, provider):
        saved = self._read().get(provider)
        if saved:
            return saved
        for name in ENVIRONMENT.get(provider, ()):
            if os.environ.get(name):
                return os.environ[name]
        return ''

    def set(self, provider, key):
        key = key.strip()
        if provider not in ENVIRONMENT:
            raise ValueError(f'{provider} does not use an API key')
        if not key or len(key) > 400 or any(c.isspace() for c in key):
            raise ValueError('That does not look like an API key')
        data = self._read()
        data[provider] = key
        self._write(data)

    def delete(self, provider):
        data = self._read()
        if data.pop(provider, None) is not None:
            self._write(data)

    def status(self):
        """Whether each provider has a key, and from where; never the key itself."""
        saved = self._read()
        result = {}
        for provider, names in ENVIRONMENT.items():
            env = next((name for name in names if os.environ.get(name)), None)
            result[provider] = {'saved': bool(saved.get(provider)), 'environment': env,
                                'ready': bool(saved.get(provider) or env)}
        return result
