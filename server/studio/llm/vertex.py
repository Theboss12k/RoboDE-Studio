"""LLM adapter for Gemini on Vertex AI (Google Cloud), billed to your Google Cloud project.

    POST https://aiplatform.googleapis.com/v1/projects/{project}/locations/global/publishers/google/models/{model}:generateContent
    POST https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent

(Vertex AI REST reference, projects.locations.publishers.models.generateContent; the global endpoint has no region
prefix in its host name.) Each call is one stateless request: systemInstruction, contents (text and inlineData images)
and generationConfig. Nothing is stored on Google's side.

There is no API key. Calls are authorised with a short-lived OAuth access token:
- on Cloud Run or Compute Engine, from the metadata server (http://metadata.google.internal), which also gives the
  project id; nothing needs to be installed and nothing is configured beyond granting the service account
  roles/aiplatform.user;
- on your own computer, from `gcloud auth application-default print-access-token` (fallback `gcloud auth
  print-access-token`), run as a child process. The token comes back on its stdout; it is never in an argument list.
The project is the profile's `project`, else GOOGLE_CLOUD_PROJECT / GCLOUD_PROJECT, else the metadata server's project
id, else `gcloud config get-value project`.
The token is cached in memory until shortly before it expires. It is never logged, stored, reported in usage or put
in an error message (every message passes through `_clean`).

Same interface as the Gemini API adapter (gemini.py): `structured(schema)` (responseMimeType application/json with
responseJsonSchema; a 400 repeats the call with the schema in the system prompt), `with_web()` (Grounding with Google
Search: tools [{"googleSearch": {}}]), images as inlineData parts, effort -> thinkingConfig.thinkingLevel, and usage
(from usageMetadata, cost from gemini.PRICES) through `on_usage`.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import threading
import time

import httpx

from .base import Image, LLMError, portable_schema, schema_instruction
from .gemini import MODELS, estimate_cost

MAX_IMAGES = 16
METADATA = 'http://metadata.google.internal/computeMetadata/v1'
METADATA_HEADERS = {'Metadata-Flavor': 'Google'}
THINKING = {'low': 'LOW', 'medium': 'MEDIUM', 'high': 'HIGH', 'xhigh': 'HIGH', 'max': 'HIGH'}
REFRESH_MARGIN_S = 120          # a token is renewed when fewer than this many seconds remain
GCLOUD_TOKEN_LIFETIME_S = 45 * 60   # gcloud prints no expiry; its tokens last an hour
SETUP_HINT = ('Run `gcloud auth application-default login` on this computer (or deploy on Cloud Run with a service '
              'account that has roles/aiplatform.user).')
_TOKEN_SHAPE = re.compile(r'(ya29\.[\w.\-]+|Bearer\s+[\w.\-]+|eyJ[\w.\-]{20,})')


def _clean(text):
    """Remove anything that looks like an access token from text that may reach a log or a message."""
    return _TOKEN_SHAPE.sub('[token removed]', str(text))


def run_command(argv, timeout=30):
    """Run a command and return (exit code, stdout, stderr); (127, '', ...) when it is not installed."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return 127, '', 'not installed'
    except subprocess.TimeoutExpired:
        return 124, '', 'timed out'
    return done.returncode, done.stdout, done.stderr


def read_dmi_product():
    """The machine's DMI product name ("Google Compute Engine" on a GCE VM; the same check Google's own client libraries use)."""
    try:
        with open('/sys/class/dmi/id/product_name', encoding='utf-8') as handle:
            return handle.read().strip()
    except OSError:
        return ''


class Credentials:
    """Access tokens and the project id, from the metadata server or gcloud. Cached; safe to share between calls."""

    def __init__(self, env=None, transport=None, runner=run_command, clock=time.monotonic, which=shutil.which,
                 dmi_product=None):
        self.env = os.environ if env is None else env
        self.dmi_product = dmi_product or read_dmi_product
        self.transport, self.runner, self.clock, self.which = transport, runner, clock, which
        self._token, self._expires, self._project = None, 0.0, None
        self._lock = threading.Lock()

    @property
    def on_cloud(self):
        # Cloud Run sets K_SERVICE; a plain Compute Engine VM sets nothing, but its firmware reports "Google Compute Engine".
        return bool(self.env.get('K_SERVICE') or self.env.get('GCE_METADATA_HOST')) or self.dmi_product().startswith('Google')

    def invalidate(self):
        with self._lock:
            self._token, self._expires = None, 0.0

    async def token(self):
        with self._lock:
            if self._token and self.clock() < self._expires - REFRESH_MARGIN_S:
                return self._token
        token, lifetime = await (self._from_metadata() if self.on_cloud else self._from_gcloud())
        with self._lock:
            self._token, self._expires = token, self.clock() + lifetime
        return token

    async def project(self, configured=''):
        if configured:
            return configured
        for name in ('GOOGLE_CLOUD_PROJECT', 'GCLOUD_PROJECT'):
            if self.env.get(name):
                return self.env[name].strip()
        if self._project:
            return self._project
        project = ''
        if self.on_cloud:
            project = (await self._metadata('/project/project-id')).text.strip()
        elif self.which('gcloud'):
            code, out, _ = await asyncio.to_thread(self.runner, ['gcloud', 'config', 'get-value', 'project'], 20)
            project = out.strip() if code == 0 and out.strip() != '(unset)' else ''
        if not project:
            raise LLMError('Vertex AI needs a Google Cloud project. Set "project" on the profile, set GOOGLE_CLOUD_PROJECT, '
                           'or run `gcloud config set project PROJECT_ID`.')
        self._project = project
        return project

    async def _metadata(self, path):
        base = self.env.get('GCE_METADATA_HOST')
        base = f'http://{base}/computeMetadata/v1' if base else METADATA
        try:
            async with httpx.AsyncClient(timeout=5, transport=self.transport) as client:
                response = await client.get(base + path, headers=METADATA_HEADERS)
        except httpx.HTTPError as error:
            raise LLMError(f'The Google metadata server could not be reached ({type(error).__name__}). On Cloud Run the '
                           'service needs no extra setup; elsewhere sign in with `gcloud auth application-default login`.') from None
        if response.status_code != 200:
            raise LLMError(f'The Google metadata server refused the request ({response.status_code})')
        return response

    async def _from_metadata(self):
        response = await self._metadata('/instance/service-accounts/default/token')
        try:
            data = response.json()
            return data['access_token'], float(data.get('expires_in') or 300)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise LLMError('The Google metadata server returned no access token') from None

    async def _from_gcloud(self):
        if not self.which('gcloud'):
            raise LLMError('No Google Cloud credentials: the gcloud command is not installed. Install the Google Cloud CLI, then '
                           + SETUP_HINT)
        failed = ''
        for argv in (['gcloud', 'auth', 'application-default', 'print-access-token'], ['gcloud', 'auth', 'print-access-token']):
            code, out, err = await asyncio.to_thread(self.runner, argv, 30)
            token = out.strip()
            if code == 0 and token:
                return token, GCLOUD_TOKEN_LIFETIME_S
            failed = failed or _clean(err.strip().splitlines()[-1][:200] if err.strip() else f'exit code {code}')
        raise LLMError(f'gcloud has no access token ({failed}). ' + SETUP_HINT)


_shared = Credentials()   # one token cache for the whole engine process


class VertexGemini:
    kind = 'vertex_gemini'

    def __init__(self, model='gemini-3.8-flash', effort='', timeout_s=240, location='global', project='', schema=None,
                 on_usage=None, web=False, transport=None, credentials=None):
        self.model, self.effort, self.timeout_s, self.schema, self.web = model, effort, timeout_s, schema, web
        self.location, self.project = (location or 'global').strip(), (project or '').strip()
        self.transport = transport
        self.credentials = credentials or _shared
        self.on_usage = on_usage
        self.last_call = None

    def _copy(self, **changes):
        fields = dict(model=self.model, effort=self.effort, timeout_s=self.timeout_s, location=self.location,
                      project=self.project, schema=self.schema, on_usage=self.on_usage, web=self.web,
                      transport=self.transport, credentials=self.credentials)
        return VertexGemini(**{**fields, **changes})

    def structured(self, schema):
        """A copy whose answers must match `schema` (responseMimeType application/json + responseJsonSchema)."""
        return self._copy(schema=schema)

    def with_web(self, timeout_s=900):
        """A copy that may use Grounding with Google Search (research mode only)."""
        return self._copy(web=True, timeout_s=max(self.timeout_s, timeout_s))

    async def invoke(self, input: str, system_prompt: str) -> str:
        return await self._run(input, system_prompt, [])

    async def invoke_multimodal(self, input: str, system_prompt: str, images: list[Image]) -> str:
        return await self._run(input, system_prompt, images[:MAX_IMAGES])

    def url(self, project):
        host = 'aiplatform.googleapis.com' if self.location == 'global' else f'{self.location}-aiplatform.googleapis.com'
        return (f'https://{host}/v1/projects/{project}/locations/{self.location}/publishers/google/models/'
                f'{self.model}:generateContent')

    def _body(self, text, system_prompt, images, schema):
        parts = []
        for image in images:
            if image.label:
                parts.append({'text': f'Image: {image.label}'})
            parts.append({'inlineData': {'mimeType': image.mime_type, 'data': image.data_base64}})
        parts.append({'text': text})
        body = {'systemInstruction': {'parts': [{'text': system_prompt or 'You are a helpful assistant.'}]},
                'contents': [{'role': 'user', 'parts': parts}]}
        config = {}
        if self.effort in THINKING:
            config['thinkingConfig'] = {'thinkingLevel': THINKING[self.effort]}
        if schema is not None:
            config['responseMimeType'] = 'application/json'
            config['responseJsonSchema'] = portable_schema(schema)
        if config:
            body['generationConfig'] = config
        if self.web:
            body['tools'] = [{'googleSearch': {}}]
        return body

    async def _post(self, body):
        project = await self.credentials.project(self.project)
        url = self.url(project)
        refreshed = False
        async with httpx.AsyncClient(timeout=self.timeout_s, transport=self.transport) as client:
            attempt = 0
            while True:
                headers = {'Authorization': f'Bearer {await self.credentials.token()}', 'Content-Type': 'application/json'}
                try:
                    response = await client.post(url, json=body, headers=headers)
                except httpx.TimeoutException:
                    raise LLMError(f'Vertex AI timed out after {self.timeout_s} s') from None
                except httpx.HTTPError as error:
                    raise LLMError(f'Vertex AI could not be reached: {type(error).__name__}') from None
                if response.status_code == 401 and not refreshed:   # the token expired or was revoked: get a new one once
                    refreshed = True
                    self.credentials.invalidate()
                    continue
                if response.status_code in (429, 500, 503) and attempt < 2:
                    attempt += 1
                    await asyncio.sleep(2 * attempt)
                    continue
                return response, project

    def _error(self, response, project):
        try:
            message = response.json().get('error', {}).get('message', '')
        except (ValueError, AttributeError):
            message = response.text
        message = _clean(str(message).strip()[:400])
        code = response.status_code
        if code == 401:
            return f'Vertex AI rejected the Google credentials (401). {SETUP_HINT} {message}'.strip()
        if code == 403:
            return (f'Vertex AI refused the call (403) for project {project}. Enable aiplatform.googleapis.com and grant the '
                    f'account roles/aiplatform.user on the project. {message}').strip()
        if code == 404:
            return (f'Vertex AI model {self.model} was not found in {self.location} for project {project} (404). '
                    f'Check the model name and the location (try "global"). {message}').strip()
        if code == 429:
            return f'Vertex AI quota or rate limit reached (429). {message}'.strip()
        return f'Vertex AI error {code}: {message}'

    async def _run(self, text, system_prompt, images):
        started = time.monotonic()
        schema, structured = self.schema, self.schema is not None
        response, project = await self._post(self._body(text, system_prompt, images, schema))
        if response.status_code == 400 and schema is not None:
            # The schema used a feature the JSON mode rejects: ask with the schema in the prompt instead.
            system_prompt = (system_prompt or '') + schema_instruction(schema)
            structured = False
            response, project = await self._post(self._body(text, system_prompt, images, None))
        data = None
        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                data = None
        if response.status_code == 200 and not isinstance(data, dict):
            data = None
        candidate = ((data or {}).get('candidates') or [{}])[0] or {}
        texts = [part['text'] for part in (candidate.get('content') or {}).get('parts') or []
                 if part.get('text') and not part.get('thought')]
        grounding = candidate.get('groundingMetadata') or {}
        queries = len(grounding.get('webSearchQueries') or [])
        pages = [(chunk.get('web') or {}).get('uri') for chunk in grounding.get('groundingChunks') or []]
        pages = [p for p in pages if p]
        usage = (data or {}).get('usageMetadata') or {}
        cached = usage.get('cachedContentTokenCount') or 0
        # the prompt count includes cached tokens; the ledger keeps them apart like the other adapters
        uncached = max(0, (usage.get('promptTokenCount') or 0) - cached) + (usage.get('toolUsePromptTokenCount') or 0)
        output = (usage.get('candidatesTokenCount') or 0) + (usage.get('thoughtsTokenCount') or 0)
        reason = candidate.get('finishReason')
        ok = data is not None and bool(texts)
        self.last_call = {'model': self.model, 'seconds': round(time.monotonic() - started, 2), 'images': len(images),
                          'input': uncached, 'cache_write': 0, 'cache_read': cached, 'output': output,
                          'cost_usd': estimate_cost(self.model, uncached, cached, output) if usage else None,
                          'turns': 1, 'structured': structured, 'ok': ok,
                          'web_calls': queries, 'pages_opened': list(dict.fromkeys(pages))[:40]}
        if self.on_usage and data is not None:
            try:
                self.on_usage(self.last_call)
            except Exception:  # counting must never break a call
                pass
        if response.status_code != 200:
            raise LLMError(self._error(response, project))
        if data is None:
            raise LLMError('Vertex AI returned an answer that is not JSON')
        if not texts:
            blocked = (data.get('promptFeedback') or {}).get('blockReason')
            raise LLMError(f'Vertex AI returned no text ({blocked or reason or "no candidate"})')
        return ''.join(texts)
