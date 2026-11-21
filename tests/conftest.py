import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / 'fixtures'


def pytest_collection_modifyitems(config, items):
    for item in items:
        if 'claude' in item.keywords and not os.environ.get('STUDIO_CLAUDE'):
            item.add_marker(pytest.mark.skip(reason='set STUDIO_CLAUDE=1 to call the real Claude CLI'))
        if 'web' in item.keywords and not os.environ.get('STUDIO_WEB'):
            item.add_marker(pytest.mark.skip(reason='set STUDIO_WEB=1 to make real network requests'))


class _BlockedLLM:
    """Stands in for a real provider in tests: any call fails like an unreachable model (LLMError)."""

    on_usage = None

    async def invoke(self, input, system_prompt):
        from studio.llm.base import LLMError
        raise LLMError('real model calls are disabled in tests (a role without a scripted adapter)')

    structured = with_web = lambda self, *args, **kwargs: self


@pytest.fixture(autouse=True)
def no_real_model_calls(monkeypatch, request):
    """A role a test left without a scripted adapter (for example `checks`) must never reach a real model."""
    if 'claude' in request.keywords:
        return
    from studio.llm import registry
    for kind in ('antigravity_cli', 'claude_cli', 'gemini', 'gemini_cli', 'vertex_gemini'):
        monkeypatch.setitem(registry.PROVIDERS, kind, lambda profile: _BlockedLLM())
