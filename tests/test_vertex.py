"""The Vertex AI Gemini adapter, against a fake HTTP layer and a fake gcloud (no network, no model call)."""
import json
import logging

import httpx
import pytest

from studio.llm import registry
from studio.llm.base import Image, LLMError, supports_images
from studio.llm.vertex import Credentials, VertexGemini

TOKEN = 'ya29.secret-access-token-for-test'
GLOBAL_URL = ('https://aiplatform.googleapis.com/v1/projects/my-proj/locations/global/publishers/google/models/'
              'gemini-3.8-flash:generateContent')


def answer(text='hello', **extra):
    return {'candidates': [{'content': {'role': 'model', 'parts': [{'text': text}]}, 'finishReason': 'STOP', **extra}],
            'usageMetadata': {'promptTokenCount': 1000, 'cachedContentTokenCount': 200, 'candidatesTokenCount': 50,
                              'thoughtsTokenCount': 30, 'toolUsePromptTokenCount': 100, 'totalTokenCount': 1180}}


class Fake:
    """Records requests; replies from a queue of (status, json) (the last one repeats)."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies) or [(200, answer())], []

    def __call__(self, request):
        self.calls.append(request)
        status, body = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return httpx.Response(status, json=body)

    @property
    def last(self):
        return self.calls[-1]

    def body(self, index=-1):
        return json.loads(self.calls[index].content)


class Gcloud:
    def __init__(self, token=TOKEN, code=0, project='my-proj', installed=True):
        self.token, self.code, self.project, self.installed, self.argvs = token, code, project, installed, []

    def which(self, name):
        return '/usr/bin/gcloud' if self.installed else None

    def __call__(self, argv, timeout=30):
        self.argvs.append(list(argv))
        if argv[1:3] == ['config', 'get-value']:
            return 0, self.project + '\n', ''
        if self.code:
            return self.code, '', f'ERROR: (gcloud.auth) You do not have an active account. {TOKEN}'
        return 0, self.token + '\n', ''


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def make(fake=None, gcloud=None, env=None, clock=None, **kw):
    fake = fake or Fake()
    gcloud = gcloud or Gcloud()
    creds = Credentials(env={} if env is None else env, transport=httpx.MockTransport(fake), runner=gcloud,
                        which=gcloud.which, clock=clock or Clock())
    kw.setdefault('project', 'my-proj')
    return VertexGemini(transport=httpx.MockTransport(fake), credentials=creds, **kw), fake, gcloud, creds


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(seconds):
        return None
    monkeypatch.setattr('studio.llm.vertex.asyncio.sleep', instant)


async def test_global_endpoint_headers_and_body():
    llm, fake, gcloud, _ = make(effort='high')
    assert await llm.invoke('what?', 'be brief') == 'hello'
    assert str(fake.last.url) == GLOBAL_URL
    assert fake.last.headers['authorization'] == f'Bearer {TOKEN}'
    assert 'x-goog-api-key' not in fake.last.headers
    assert fake.body() == {'systemInstruction': {'parts': [{'text': 'be brief'}]},
                           'contents': [{'role': 'user', 'parts': [{'text': 'what?'}]}],
                           'generationConfig': {'thinkingConfig': {'thinkingLevel': 'HIGH'}}}
    assert all(TOKEN not in ' '.join(argv) for argv in gcloud.argvs)   # the token is never in an argument list


async def test_regional_endpoint():
    llm, fake, *_ = make(location='us-central1')
    await llm.invoke('x', 's')
    assert str(fake.last.url) == ('https://us-central1-aiplatform.googleapis.com/v1/projects/my-proj/locations/us-central1/'
                                  'publishers/google/models/gemini-3.8-flash:generateContent')


async def test_no_effort_no_generation_config_and_default_system():
    llm, fake, *_ = make()
    await llm.invoke('x', '')
    body = fake.body()
    assert 'generationConfig' not in body and 'tools' not in body
    assert body['systemInstruction']['parts'][0]['text'] == 'You are a helpful assistant.'


async def test_images_are_inline_data():
    llm, fake, *_ = make()
    assert supports_images(llm)
    await llm.invoke_multimodal('look', 's', [Image('QUJD', 'image/png', 'front'), Image('REVG')])
    parts = fake.body()['contents'][0]['parts']
    assert parts == [{'text': 'Image: front'}, {'inlineData': {'mimeType': 'image/png', 'data': 'QUJD'}},
                     {'inlineData': {'mimeType': 'image/jpeg', 'data': 'REVG'}}, {'text': 'look'}]
    assert llm.last_call['images'] == 2


async def test_structured_sets_json_mode_with_portable_schema():
    schema = {'type': 'object', 'properties': {'p': {'type': 'array', 'prefixItems': [{'type': 'number'}] * 2, 'x-optional': True}}}
    llm, fake, *_ = make()
    out = await llm.structured(schema).invoke('x', 's')
    config = fake.body()['generationConfig']
    assert config['responseMimeType'] == 'application/json'
    assert 'prefixItems' not in json.dumps(config['responseJsonSchema']) and 'x-optional' not in json.dumps(config)
    assert out == 'hello' and llm.schema is None   # a copy; the original is untouched


async def test_structured_falls_back_to_prompt_on_400():
    llm, fake, *_ = make(fake=Fake((400, {'error': {'message': 'bad schema'}}), (200, answer('{"a": 1}'))))
    out = await llm.structured({'type': 'object', 'properties': {'a': {'type': 'integer'}}}).invoke('x', 'sys')
    assert out == '{"a": 1}' and len(fake.calls) == 2
    second = fake.body()
    assert 'generationConfig' not in second and 'JSON schema' in second['systemInstruction']['parts'][0]['text']


async def test_with_web_uses_google_search_and_reports_grounding():
    reply = answer('found', groundingMetadata={'webSearchQueries': ['a', 'b'],
                                               'groundingChunks': [{'web': {'uri': 'https://x.example/1'}}, {'web': {'uri': 'https://x.example/1'}},
                                                                   {'web': {'uri': 'https://y.example/2'}}]})
    llm, fake, *_ = make(fake=Fake((200, reply)), timeout_s=60)
    web = llm.with_web()
    assert web.timeout_s == 900 and llm.web is False
    await web.invoke('q', 's')
    assert fake.body()['tools'] == [{'googleSearch': {}}]
    assert web.last_call['web_calls'] == 2 and web.last_call['pages_opened'] == ['https://x.example/1', 'https://y.example/2']


async def test_usage_from_usage_metadata():
    seen = []
    llm, *_ = make(model='gemini-3.8-flash')
    llm.on_usage = seen.append
    await llm.invoke('x', 's')
    entry = seen[0]
    assert (entry['input'], entry['cache_read'], entry['output']) == (900, 200, 80)   # 800 uncached prompt + 100 tool use
    assert entry['cost_usd'] == round((900 * 0.75 + 80 * 3.75 + 200 * 0.075) / 1e6, 6)
    assert entry['ok'] and entry['turns'] == 1 and not entry['structured']
    assert TOKEN not in json.dumps(entry)


async def test_thought_parts_are_not_the_answer():
    reply = answer()
    reply['candidates'][0]['content']['parts'] = [{'text': 'thinking...', 'thought': True}, {'text': 'answer'}]
    llm, *_ = make(fake=Fake((200, reply)))
    assert await llm.invoke('x', 's') == 'answer'


@pytest.mark.parametrize('status,needle', [(401, 'gcloud auth application-default login'), (403, 'roles/aiplatform.user'),
                                           (404, 'was not found'), (429, 'quota'), (500, 'error 500')])
async def test_error_mapping(status, needle):
    llm, fake, *_ = make(fake=Fake((status, {'error': {'message': f'detail {TOKEN}'}})))
    with pytest.raises(LLMError) as raised:
        await llm.invoke('x', 's')
    assert needle in str(raised.value) and TOKEN not in str(raised.value)
    if status == 403:
        assert 'aiplatform.googleapis.com' in str(raised.value) and 'my-proj' in str(raised.value)
    if status == 429 or status == 500:
        assert len(fake.calls) == 3   # retried twice


async def test_429_then_success():
    llm, fake, *_ = make(fake=Fake((429, {'error': {'message': 'busy'}}), (200, answer('ok'))))
    assert await llm.invoke('x', 's') == 'ok' and len(fake.calls) == 2


async def test_empty_and_blocked_answers():
    llm, *_ = make(fake=Fake((200, {'candidates': [{'finishReason': 'SAFETY'}]})))
    with pytest.raises(LLMError, match='SAFETY'):
        await llm.invoke('x', 's')


async def test_timeout_and_network_errors_name_no_token():
    def boom(request):
        raise httpx.ConnectError('failed', request=request)
    llm, *_ = make(fake=boom)
    with pytest.raises(LLMError, match='ConnectError') as raised:
        await llm.invoke('x', 's')
    assert TOKEN not in str(raised.value)


async def test_token_cached_then_refreshed_before_expiry():
    clock = Clock()
    llm, fake, gcloud, _ = make(clock=clock)
    await llm.invoke('a', 's')
    await llm.invoke('b', 's')
    token_runs = [a for a in gcloud.argvs if 'print-access-token' in a]
    assert len(token_runs) == 1
    clock.now += 45 * 60 - 60   # inside the refresh margin of the 45 minute lifetime
    await llm.invoke('c', 's')
    assert len([a for a in gcloud.argvs if 'print-access-token' in a]) == 2


async def test_401_refreshes_the_token_once():
    llm, fake, gcloud, _ = make(fake=Fake((401, {'error': {'message': 'expired'}}), (200, answer('fine'))))
    assert await llm.invoke('x', 's') == 'fine'
    assert len([a for a in gcloud.argvs if 'print-access-token' in a]) == 2


async def test_gcloud_falls_back_to_user_token_command():
    class Partial(Gcloud):
        def __call__(self, argv, timeout=30):
            if argv[1:3] == ['auth', 'application-default']:
                self.argvs.append(list(argv))
                return 1, '', 'no ADC'
            return super().__call__(argv, timeout)
    llm, fake, gcloud, _ = make(gcloud=Partial())
    await llm.invoke('x', 's')
    assert gcloud.argvs[-1] == ['gcloud', 'auth', 'print-access-token']
    assert fake.last.headers['authorization'] == f'Bearer {TOKEN}'


async def test_gcloud_not_signed_in_and_not_installed():
    llm, *_ = make(gcloud=Gcloud(code=1))
    with pytest.raises(LLMError) as raised:
        await llm.invoke('x', 's')
    assert 'gcloud auth application-default login' in str(raised.value) and TOKEN not in str(raised.value)
    llm, *_ = make(gcloud=Gcloud(installed=False))
    with pytest.raises(LLMError, match='not installed'):
        await llm.invoke('x', 's')


async def test_project_resolution_order():
    llm, fake, gcloud, _ = make(project='', env={'GOOGLE_CLOUD_PROJECT': 'from-env'})
    await llm.invoke('x', 's')
    assert '/projects/from-env/' in str(fake.last.url)
    assert not any(a[1:3] == ['config', 'get-value'] for a in gcloud.argvs)
    llm, fake, gcloud, _ = make(project='', gcloud=Gcloud(project='from-gcloud'))
    await llm.invoke('x', 's')
    assert '/projects/from-gcloud/' in str(fake.last.url)
    llm, *_ = make(project='', gcloud=Gcloud(project='(unset)'))
    with pytest.raises(LLMError, match='needs a Google Cloud project'):
        await llm.invoke('x', 's')


async def test_cloud_run_uses_the_metadata_server_not_gcloud():
    def handler(request):
        assert request.headers.get('Metadata-Flavor') == 'Google'
        if request.url.host == 'metadata.google.internal':
            if request.url.path.endswith('/project/project-id'):
                return httpx.Response(200, text='run-proj')
            assert request.url.path.endswith('/instance/service-accounts/default/token')
            return httpx.Response(200, json={'access_token': TOKEN, 'expires_in': 3000, 'token_type': 'Bearer'})
        assert request.headers['authorization'] == f'Bearer {TOKEN}'
        return httpx.Response(200, json=answer('from cloud run'))

    calls = []

    def recording(request):
        calls.append(str(request.url))
        if request.url.host != 'metadata.google.internal':   # the API call carries no Metadata-Flavor header
            assert request.headers['authorization'] == f'Bearer {TOKEN}'
            return httpx.Response(200, json=answer('from cloud run'))
        return handler(request)

    gcloud = Gcloud()
    creds = Credentials(env={'K_SERVICE': 'robode-studio'}, transport=httpx.MockTransport(recording), runner=gcloud,
                        which=gcloud.which)
    llm = VertexGemini(transport=httpx.MockTransport(recording), credentials=creds)
    assert await llm.invoke('x', 's') == 'from cloud run'
    assert gcloud.argvs == []
    assert any('/projects/run-proj/' in c for c in calls)
    assert calls[0].startswith('http://metadata.google.internal/computeMetadata/v1/')


async def test_metadata_server_unreachable_message():
    def down(request):
        raise httpx.ConnectError('nope', request=request)
    gcloud = Gcloud()
    creds = Credentials(env={'K_SERVICE': 'x', 'GOOGLE_CLOUD_PROJECT': 'p'}, transport=httpx.MockTransport(down), runner=gcloud,
                        which=gcloud.which)
    with pytest.raises(LLMError, match='metadata server'):
        await VertexGemini(transport=httpx.MockTransport(down), credentials=creds).invoke('x', 's')


async def test_token_never_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    llm, *_ = make(fake=Fake((403, {'error': {'message': f'denied {TOKEN}'}})))
    with pytest.raises(LLMError):
        await llm.invoke('x', 's')
    assert TOKEN not in caplog.text and TOKEN not in repr(llm.last_call)


def test_registry_builds_the_adapter_without_a_key_and_lists_profiles(tmp_path, monkeypatch):
    for name in ('K_SERVICE', 'STUDIO_LLM_DEFAULT'):
        monkeypatch.delenv(name, raising=False)
    assert 'vertex_gemini' in registry.PROVIDERS and 'vertex_gemini' not in registry.KEYED
    ids = {p['id']: p for p in registry.default_profiles()}
    assert ids['vertex_pro']['kind'] == 'vertex_gemini' and ids['vertex_pro']['location'] == 'global'
    assert set(ids) >= {'vertex_pro', 'vertex_flash', 'vertex_flash_lite'}
    llm = registry.vertex_gemini({'model': 'gemini-3.8-flash', 'location': 'us-east5', 'project': 'p', 'effort': 'low', 'timeout_s': 99})
    assert (llm.location, llm.project, llm.effort, llm.timeout_s) == ('us-east5', 'p', 'low', 99)
    profile = registry.Profile(name='V', kind='vertex_gemini', model='gemini-3.8-flash', location='us-central1', project='my-proj-1')
    assert profile.location == 'us-central1'
    with pytest.raises(Exception):
        registry.Profile(name='V', kind='vertex_gemini', location='US Central')


def test_role_defaults_switch_to_vertex_on_cloud_run(monkeypatch):
    monkeypatch.delenv('K_SERVICE', raising=False)
    monkeypatch.delenv('STUDIO_LLM_DEFAULT', raising=False)
    assert registry.default_roles()['spec'] == 'gemini_pro'
    assert registry.default_roles({'K_SERVICE': 'x'}) == registry.VERTEX_ROLES
    assert registry.default_roles({'STUDIO_LLM_DEFAULT': 'vertex'})['objects'] == 'vertex_flash_lite'
    assert registry.default_roles({'STUDIO_LLM_DEFAULT': 'gemini'})['spec'] == 'gemini_pro'
