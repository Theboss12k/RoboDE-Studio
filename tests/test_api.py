import asyncio
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from fixtures_specs import bottle_spec
from studio.api.app import create_app
from studio.catalog.fetch import Downloaded
from studio.llm.scripted import ScriptedLLM
from studio_scripts import llms, scene_dict
from test_catalog import answer as search_answer, values_answer

DATASHEET = (Path(__file__).parent / 'fixtures' / 'arm_datasheet.md').read_text()


def analysis_reply(input, system, images):
    items = json.loads(input.split('Differences (JSON):\n', 1)[1])
    return {'differences': [{**d, 'impact': f'{d["topic"]}: fine for this task.'} for d in items]}


def overrides():
    kw = llms()
    answers = [{'questions': [{'id': 'cycle', 'text': 'Cycle time?', 'kind': 'number', 'unit': 's', 'default': 6}]}, None]
    from studio_scripts import spec_dict

    def spec_reply(i, s, im):
        return answers[0] if 'cycle' not in i.split('answers_so_far')[1][:40] else {'spec': spec_dict()}
    kw['spec'] = ScriptedLLM([('requirements engineer', spec_reply)])
    moved = bottle_spec()
    moved.markers[1].position = (0.5, -0.5, 0.75)
    kw['revise'] = ScriptedLLM([('change an existing', {'targets': ['spec', 'scene'], 'spec': moved.model_dump(mode='json'),
                                                        'scene': scene_dict(moved), 'summary': 'B 20 cm further'})])
    kw['search'] = ScriptedLLM([('find real robots', search_answer())])
    kw['extract'] = ScriptedLLM([('manufacturer\'s datasheet', values_answer())])
    kw['analysis'] = ScriptedLLM([('explain, in plain', analysis_reply)])
    return kw


async def fake_download(url):
    return Downloaded(bytes=DATASHEET.encode(), content_type='text/markdown', final_url=url, filename='ra6.md')


@pytest.fixture
async def client(tmp_path):
    app = create_app(tmp_path, overrides(), download=fake_download)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        yield c


async def wait(client, job):
    while job['status'] == 'running':
        await asyncio.sleep(0.02)
        job = (await client.get(f'/api/jobs/{job["id"]}')).json()
    return job


async def post_job(client, path, body=None):
    response = await client.post(path, json=body or {})
    assert response.status_code == 200, response.text
    return await wait(client, response.json())


async def test_full_flow(client):
    assert (await client.get('/api/health')).json()['ok']
    fams = (await client.get('/api/families')).json()
    assert {f['name'] for f in fams} >= {'serial_arm', 'gantry', 'mobile_base', 'mobile_manipulator'}

    p = (await client.post('/api/projects', json={'brief': 'Move a 0.3 kg bottle from A to B'})).json()
    pid = p['id']
    assert p['stage'] == 'brief' and p['reference'] is None and p['versions'] == []
    assert pid in [x['id'] for x in (await client.get('/api/projects')).json()]
    note = (await client.put(f'/api/projects/{pid}/reference', json={'text': 'A bench-top task.'})).json()
    assert note['reference']['text'] == 'A bench-top task.'

    job = await post_job(client, f'/api/projects/{pid}/design', {'answers': {}})
    assert job['status'] == 'done' and job['result']['status'] == 'questions'
    assert (await client.get(f'/api/projects/{pid}')).json()['questions'][0]['id'] == 'cycle'
    job = await post_job(client, f'/api/projects/{pid}/design', {'answers': {'cycle': 6}})
    assert job['result'] == {'status': 'designed', 'version': 1}, job
    assert 'Working out the requirements' in job['progress']

    v1 = (await client.get(f'/api/projects/{pid}/versions/1')).json()
    names = {j['name'] for j in v1['parsed']['joints'] if j['type'] != 'fixed'}
    assert names and set(v1['sim']['frames'][0]['joints']) <= names
    project = (await client.get(f'/api/projects/{pid}')).json()
    assert project['versions'] == [1] and project['answers'] == {'cycle': 6} and project['stage'] == 'designed'

    job = await post_job(client, f'/api/projects/{pid}/revise', {'request': 'make it reach 20 cm further'})
    assert job['result'] == {'version': 2}, job
    assert (await client.get(f'/api/projects/{pid}/versions/1')).json()['spec'] == v1['spec']

    assert (await client.post(f'/api/projects/{pid}/approve', json={'version': 2})).json()['approved_version'] == 2
    assert (await client.get(f'/api/projects/{pid}/versions/2')).json()['approved'] is True

    job = await post_job(client, f'/api/projects/{pid}/search')
    assert [c['id'] for c in job['result']['candidates']][0] == 'acme_ra_6'
    cands = (await client.get(f'/api/projects/{pid}/candidates')).json()
    assert isinstance(cands, list) and cands[0]['id'] == 'acme_ra_6'

    job = await post_job(client, f'/api/projects/{pid}/candidates/acme_ra_6/analyse', {'datasheet_url': 'https://acme.example/ra6.md'})
    assert job['status'] == 'done', job
    result = job['result']
    assert result['parsed']['joints'] and result['differences'] and all(d['impact'] for d in result['differences'])
    verified = {v['key'] for v in result['manufacturer_spec']['values'] if v['verified']}
    assert {'payload_kg', 'reach_m'} <= verified and 'mass_kg' not in verified
    again = (await client.get(f'/api/projects/{pid}/analysis/acme_ra_6')).json()
    assert again['candidate_id'] == 'acme_ra_6' and again['parsed']
    project = (await client.get(f'/api/projects/{pid}')).json()
    assert project['analyses'] == ['acme_ra_6'] and project['candidates_count'] >= 1

    decided = (await client.post(f'/api/projects/{pid}/decision', json={'candidate_id': 'acme_ra_6', 'action': 'approve'})).json()
    assert decided['decision'] == {'candidate_id': 'acme_ra_6', 'action': 'approve'}

    response = await client.get(f'/api/projects/{pid}/export')
    assert response.headers['content-type'] == 'application/zip'
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    files = set(archive.namelist())
    assert {'design.urdf', 'design.glb', 'spec.json', 'requirements.json', 'scene.json', 'sim_summary.json', 'README.txt',
            'candidates/acme_ra_6/analysis.json', 'candidates/acme_ra_6/manufacturer_values.json', 'decision.json'} <= files
    summary = json.loads(archive.read('sim_summary.json'))
    assert summary['checks'] and 'frames' not in summary
    assert 'not tested on hardware' in archive.read('README.txt').decode().lower() or 'hardware' in archive.read('README.txt').decode()

    assert (await client.delete(f'/api/projects/{pid}')).json() == {'ok': True}
    assert (await client.get(f'/api/projects/{pid}')).status_code == 404


async def test_llm_settings_and_usage(client):
    settings = (await client.get('/api/llm')).json()
    assert 'profiles' in settings and 'roles' in settings
    assert (await client.get('/api/llm/usage?period=7d')).json()['totals']['calls'] == 0
    assert (await client.get('/api/llm/usage?period=forever')).status_code == 422
    assert (await client.get('/api/projects/p_0000000000')).status_code == 404
    assert (await client.get('/api/projects/..%2F..%2Fx')).status_code in (404, 422)


async def test_upload_reference_and_datasheet_errors(client):
    pid = (await client.post('/api/projects', json={'brief': 'x'})).json()['id']
    bad = await client.post(f'/api/projects/{pid}/reference/files?filename=a.exe', content=b'MZ')
    assert bad.status_code == 422
    png = b'\x89PNG\r\n\x1a\n' + b'0' * 20
    ok = await client.post(f'/api/projects/{pid}/reference/files?filename=photo.png', content=png)
    assert ok.json()['reference']['files'] == [{'name': 'photo.png', 'kind': 'image'}]
    cleared = (await client.delete(f'/api/projects/{pid}/reference')).json()
    assert cleared['reference'] is None


async def test_ssrf_datasheet_url_fails_with_upload_hint(tmp_path):
    app = create_app(tmp_path, overrides())   # the real safe_download
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        pid = (await client.post('/api/projects', json={'brief': 'x'})).json()['id']
        await post_job(client, f'/api/projects/{pid}/design', {'answers': {}})
        await post_job(client, f'/api/projects/{pid}/design', {'answers': {'cycle': 6}})
        await client.post(f'/api/projects/{pid}/approve', json={'version': 1})
        await post_job(client, f'/api/projects/{pid}/search')
        job = await post_job(client, f'/api/projects/{pid}/candidates/acme_ra_6/analyse', {'datasheet_url': 'http://127.0.0.1/secret.pdf'})
        assert job['status'] == 'error' and job['error_code'] == 'datasheet_download'
        assert job['error'].startswith('Datasheet download failed:') and 'Upload' in job['error']
        # the user uploads the datasheet instead
        up = await client.post(f'/api/projects/{pid}/candidates/acme_ra_6/datasheet?filename=ra6.md', content=DATASHEET.encode())
        assert up.json()['ok']
        job = await post_job(client, f'/api/projects/{pid}/candidates/acme_ra_6/analyse')
        assert job['status'] == 'done', job


async def test_analyse_needs_approved_design(client):
    pid = (await client.post('/api/projects', json={'brief': 'x'})).json()['id']
    job = await post_job(client, f'/api/projects/{pid}/search')
    assert job['status'] == 'error' and 'Approve' in job['error']
