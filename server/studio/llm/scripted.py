"""A fixture adapter for tests: it replays prepared answers instead of calling a model.

`ScriptedLLM(rules)` picks the answer from the first rule whose `match` text appears in the system prompt
(or input). An answer may be a string, a dict (sent as JSON) or a callable(input, system_prompt, images).
Results from this adapter are fixture results, never model results.
"""
import json

from .base import Image, LLMError


class NoScriptedAnswer(LLMError, AssertionError):
    """No rule matched the prompt: an LLMError for callers that treat a failed model call as non-fatal (an AssertionError
    too, so tests that expect one still see it)."""


class ScriptedLLM:
    kind = 'scripted'
    model = 'fixture'

    def __init__(self, rules):
        self.rules = list(rules)
        self.calls = []

    def structured(self, schema):
        return self

    def with_web(self, timeout_s=900):  # replays answers; nothing is searched
        return self

    async def invoke(self, input: str, system_prompt: str) -> str:
        return self._answer(input, system_prompt, [])

    async def invoke_multimodal(self, input: str, system_prompt: str, images: list[Image]) -> str:
        return self._answer(input, system_prompt, images)

    def _answer(self, input, system_prompt, images):
        self.calls.append({'input': input, 'system_prompt': system_prompt, 'images': len(images)})
        for match, answer in self.rules:
            if match in system_prompt or match in input:
                if callable(answer):
                    answer = answer(input, system_prompt, images)
                return answer if isinstance(answer, str) else json.dumps(answer)
        raise NoScriptedAnswer('No scripted answer for this prompt: ' + system_prompt[:120])
