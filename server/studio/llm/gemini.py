"""LLM adapter for Google Gemini, through the Gemini API's Interactions endpoint.

    POST https://generativelanguage.googleapis.com/v1beta/interactions

Google recommends this endpoint for new projects (checked 10 November 2026). Each call is one stateless interaction
(`store: false`): the system prompt, the text and any images go in one request, and nothing is kept on Google's
side between calls, so the studio's own prompts stay the only state.

- Images are inline base64 parts: {"type": "image", "data", "mime_type"}.
- `structured(schema)` asks for JSON output that matches the schema (`response_format`). If the API rejects the
  schema, the call is repeated once with the schema written into the system prompt instead, so a schema feature
  Gemini doesn't support never stops a job.
- `with_web()` turns on Grounding with Google Search (the `google_search` tool). This is the research role's
  equivalent of the Claude CLI's WebSearch/WebFetch. Gemini searches and reads results on Google's side; nothing
  is fetched on this machine.
- Effort maps to Gemini's thinking level: low, medium, high (xhigh and max use high).

The API key is never part of a profile: the registry passes it in from runtime/llm/keys.json or the
GEMINI_API_KEY / GOOGLE_API_KEY environment variable. It is sent only in the x-goog-api-key header to Google.
Usage is reported through `on_usage`, like the Claude CLI adapter; `cost_usd` is estimated from PRICES below.
"""
import asyncio
import time

import httpx

from .base import Image, LLMError, schema_instruction

BASE_URL = 'https://generativelanguage.googleapis.com/v1beta'
API_REVISION = '2026-05-20'  # the revision Google's REST examples send; pinned so response shapes don't shift
MAX_IMAGES = 16

# Suggested models (the field accepts any Gemini model id). Standard paid tier, USD per 1M tokens, from
# Google's pricing page on 10 November 2026: (input, output incl. thinking, cached input). Prices change; an
# unknown model gets cost_usd None rather than a guess. Grounding with Google Search is not included: 5,000
# searches a month are free on Gemini 3 and later, then $14 per 1,000.
PRICES = {
    'gemini-3.8-flash': (0.75, 3.75, 0.075),
    'gemini-4-carbon': (2.00, 10.00, 0.10),
    'gemini-4-argon': (2.00, 10.00, 0.10),
    'gemini-3.1-pro-preview': (2.00, 12.00, 0.20),  # above 200k input tokens: 4.00, 18.00, 0.40
    'gemini-3.6-flash': (0.75, 3.75, 0.075),
    'gemini-3.5-flash-lite': (0.30, 2.50, 0.03),
    'gemini-3.1-flash-lite': (0.25, 1.50, 0.025),
    'gemini-robotics-er-2-preview': (1.00, 5.00, 0.10),
}
LONG_PROMPT = {'gemini-3.1-pro-preview': (200_000, (4.00, 18.00, 0.40))}
MODELS = tuple(PRICES)
THINKING = {'low': 'low', 'medium': 'medium', 'high': 'high', 'xhigh': 'high', 'max': 'high'}


def estimate_cost(model, input_tokens, cached, output):
    price = PRICES.get(model)
    if price is None:
        return None
    threshold, long_price = LONG_PROMPT.get(model, (None, None))
    if threshold is not None and input_tokens + cached > threshold:
        price = long_price
    return round((input_tokens * price[0] + output * price[1] + cached * price[2]) / 1e6, 6)


class GeminiAPI:
    kind = 'gemini'

    def __init__(self, model='gemini-3.8-flash', effort='', timeout_s=240, api_key='', schema=None, on_usage=None,
                 web=False, base_url=BASE_URL, transport=None):
        self.model, self.effort, self.timeout_s, self.schema, self.web = model, effort, timeout_s, schema, web
        self.api_key, self.base_url, self.transport = api_key, base_url.rstrip('/'), transport
        self.on_usage = on_usage  # callable(entry) the registry attaches to count tokens
        self.last_call = None

    def _copy(self, **changes):
        fields = dict(model=self.model, effort=self.effort, timeout_s=self.timeout_s, api_key=self.api_key,
                      schema=self.schema, on_usage=self.on_usage, web=self.web, base_url=self.base_url,
                      transport=self.transport)
        return GeminiAPI(**{**fields, **changes})

    def structured(self, schema):
        """A copy whose answers must match `schema` (Gemini's JSON output mode)."""
        return self._copy(schema=schema)

    def with_web(self, timeout_s=900):
        """A copy that may use Grounding with Google Search (research mode only)."""
        return self._copy(web=True, timeout_s=max(self.timeout_s, timeout_s))

    async def invoke(self, input: str, system_prompt: str) -> str:
        return await self._run(input, system_prompt, [])

    async def invoke_multimodal(self, input: str, system_prompt: str, images: list[Image]) -> str:
        return await self._run(input, system_prompt, images[:MAX_IMAGES])

    def _body(self, text, system_prompt, images, schema):
        parts = []
        for image in images:
            if image.label:
                parts.append({'type': 'text', 'text': f'Image: {image.label}'})
            parts.append({'type': 'image', 'data': image.data_base64, 'mime_type': image.mime_type})
        parts.append({'type': 'text', 'text': text})
        body = {'model': self.model, 'system_instruction': system_prompt or 'You are a helpful assistant.',
                'input': parts, 'store': False}
        if self.effort in THINKING:
            body['generation_config'] = {'thinking_level': THINKING[self.effort]}
        if schema is not None:
            body['response_format'] = {'type': 'text', 'mime_type': 'application/json', 'schema': schema}
        if self.web:
            body['tools'] = [{'type': 'google_search'}]
        return body

    async def _post(self, body):
        if not self.api_key:
            raise LLMError('No Gemini API key. Add one under AI models, or set GEMINI_API_KEY before starting the studio.')
        headers = {'x-goog-api-key': self.api_key, 'Content-Type': 'application/json', 'Api-Revision': API_REVISION}
        async with httpx.AsyncClient(timeout=self.timeout_s, transport=self.transport) as client:
            for attempt in range(3):
                try:
                    response = await client.post(f'{self.base_url}/interactions', json=body, headers=headers)
                except httpx.TimeoutException:
                    raise LLMError(f'Gemini timed out after {self.timeout_s} s') from None
                except httpx.HTTPError as error:  # the message names the host, never the key
                    raise LLMError(f'Gemini could not be reached: {type(error).__name__}') from None
                if response.status_code in (429, 500, 503) and attempt < 2:  # busy or rate-limited: back off and retry
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                return response

    @staticmethod
    def _error(response):
        try:
            message = response.json().get('error', {}).get('message', '')
        except ValueError:
            message = response.text
        message = str(message).strip()[:400]
        if response.status_code in (401, 403):
            return f'Gemini rejected the API key ({response.status_code}). Check the key under AI models. {message}'.strip()
        if response.status_code == 429:
            return f'Gemini rate limit or quota reached (429). {message}'.strip()
        if response.status_code == 404:
            return f'Gemini model {message or "not found"} (404). Check the model name.'
        return f'Gemini error {response.status_code}: {message}'

    async def _run(self, text, system_prompt, images):
        started = time.monotonic()
        schema, structured = self.schema, self.schema is not None
        response = await self._post(self._body(text, system_prompt, images, schema))
        if response.status_code == 400 and schema is not None:
            # The schema used a feature Gemini's JSON mode rejects: ask with the schema in the prompt instead.
            system_prompt = (system_prompt or '') + schema_instruction(schema)
            structured = False
            response = await self._post(self._body(text, system_prompt, images, None))
        data = None
        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                data = None
        texts, queries, pages = [], 0, []
        for step in (data or {}).get('steps') or []:
            kind = step.get('type')
            if kind == 'model_output':
                for part in step.get('content') or []:
                    if part.get('type') == 'text' and part.get('text'):
                        texts.append(part['text'])
                    for note in part.get('annotations') or []:
                        if note.get('type') == 'url_citation' and note.get('url'):
                            pages.append(note['url'])
            elif kind == 'google_search_call':
                arguments = step.get('arguments') or {}
                queries += len(arguments.get('queries') or []) or (1 if arguments.get('query') else 0) or 1
        usage = (data or {}).get('usage') or {}
        cached = usage.get('total_cached_tokens') or 0
        # Gemini counts cached tokens inside the input total; the ledger keeps them apart like the Claude adapter.
        uncached = max(0, (usage.get('total_input_tokens') or 0) - cached) + (usage.get('total_tool_use_tokens') or 0)
        output = (usage.get('total_output_tokens') or 0) + (usage.get('total_thought_tokens') or 0)
        grounding = sum(item.get('count') or 0 for item in usage.get('grounding_tool_count') or [])
        status = (data or {}).get('status')
        ok = data is not None and status in (None, 'completed') and bool(texts)
        self.last_call = {'model': self.model, 'seconds': round(time.monotonic() - started, 2), 'images': len(images),
                          'input': uncached, 'cache_write': 0, 'cache_read': cached, 'output': output,
                          'cost_usd': estimate_cost(self.model, uncached, cached, output) if usage else None,
                          'turns': 1, 'structured': structured, 'ok': ok,
                          'web_calls': grounding or queries, 'pages_opened': list(dict.fromkeys(pages))[:40]}
        if self.on_usage and data is not None:
            try:
                self.on_usage(self.last_call)
            except Exception:  # counting must never break a call
                pass
        if response.status_code != 200:
            raise LLMError(self._error(response))
        if data is None:
            raise LLMError('Gemini returned an answer that is not JSON')
        if status not in (None, 'completed'):
            raise LLMError(f'Gemini stopped with status "{status}"')
        if not texts:
            raise LLMError('Gemini returned no text')
        return ''.join(texts)
