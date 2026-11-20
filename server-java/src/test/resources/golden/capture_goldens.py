#!/usr/bin/env python3
"""Capture golden HTTP responses of the pre-migration RoboDE Studio FastAPI server.

Run from the backup folder with the live repo's venv:
    PYTHONPATH=<backup>/server <venv>/bin/python <backup>/capture_goldens.py

Everything is scripted: ScriptedLLM answers (fixture data, not model output), no network. The fake datasheet
download serves tests/fixtures/arm_datasheet.md. The one real download path is the SSRF check, which rejects
http://127.0.0.1 before any connection is made.

Output: one JSON file per request in GOLDEN_DIR, named NN_<name>.json, each holding
{method, path, request_body, status, response_body} (+ frames_count where a body carries sim frames).
Job polls are not saved one by one: a job's start response is saved as <name>.json and its final record
(GET /api/jobs/<job_id>) as <name>_final.json.
"""
import asyncio
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN_DIR = Path.home() / 'Downloads/RoboDE-Studio/server-java/src/test/resources/golden'
sys.path[:0] = [str(HERE / 'server'), str(HERE / 'tests')]

SCRATCH = HERE / '.golden_runtimes'          # temp runtimes live here and are deleted at the end
SCRATCH.mkdir(exist_ok=True)
os.environ['STUDIO_RUNTIME'] = str(SCRATCH / 'unused_default_runtime')  # never touch a real runtime folder

from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import create_app  # noqa: E402
from studio.catalog.fetch import Downloaded, safe_download  # noqa: E402
from studio.llm.scripted import ScriptedLLM  # noqa: E402
from studio_scripts import llms, scene_dict, spec_dict  # noqa: E402
from test_catalog import answer as search_answer, values_answer  # noqa: E402
from test_api import analysis_reply  # noqa: E402  (same fixture reply as the pytest flow)
from fixtures_specs import bottle_spec  # noqa: E402

DATASHEET = (HERE / 'tests' / 'fixtures' / 'arm_datasheet.md').read_text()
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 20
SPEC_DELAY_S = 0.3   # keeps the design job running long enough to test the 409 "already working" answer
JOB_TIMEOUT_S = 120

ISO_TIME = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
ABS_PATH = re.compile(r'(?<![\w.])/(?:Users|private|var|tmp|Volumes)/[^\s"\\,\]\}]*')
TODAY = date.today().isoformat()


class SlowScripted(ScriptedLLM):
    """ScriptedLLM that waits first, so a job stays 'running' for a while."""

    def __init__(self, rules, delay):
        super().__init__(rules)
        self.delay = delay

    async def invoke(self, input, system_prompt):
        await asyncio.sleep(self.delay)
        return self._answer(input, system_prompt, [])

    async def invoke_multimodal(self, input, system_prompt, images):
        await asyncio.sleep(self.delay)
        return self._answer(input, system_prompt, images)


def overrides():
    """Same scripted answers as tests/test_api.py overrides(), with a short delay on the spec role."""
    kw = llms()
    answers = [{'questions': [{'id': 'cycle', 'text': 'Cycle time?', 'kind': 'number', 'unit': 's', 'default': 6}]}, None]

    def spec_reply(i, s, im):
        return answers[0] if 'cycle' not in i.split('answers_so_far')[1][:40] else {'spec': spec_dict()}
    kw['spec'] = SlowScripted([('requirements engineer', spec_reply)], SPEC_DELAY_S)
    moved = bottle_spec()
    moved.markers[1].position = (0.5, -0.5, 0.75)
    kw['revise'] = ScriptedLLM([('change an existing', {'targets': ['spec', 'scene'],
                                                        'spec': moved.model_dump(mode='json'),
                                                        'scene': scene_dict(moved), 'summary': 'B 20 cm further'})])
    kw['search'] = ScriptedLLM([('find real robots', search_answer())])
    kw['extract'] = ScriptedLLM([('manufacturer\'s datasheet', values_answer())])
    kw['analysis'] = ScriptedLLM([('explain, in plain', analysis_reply)])
    return kw


async def fake_download(url):
    return Downloaded(bytes=DATASHEET.encode(), content_type='text/markdown', final_url=url, filename='ra6.md')


async def mixed_download(url):
    """Vendor host -> fixture datasheet (no network). Anything else -> the real safe_download."""
    if url.startswith('https://acme.example/'):
        return await fake_download(url)
    return await safe_download(url)


class Sink:
    """Numbers, scrubs and writes the golden files. Ids are aliased to stable placeholders everywhere."""

    def __init__(self, folder):
        self.dir = folder
        self.n = 0
        self.aliases = {}
        self.roots = {str(HERE), str(SCRATCH)}

    def alias(self, real, placeholder):
        if real:
            self.aliases[str(real)] = placeholder

    def scrub_text(self, text):
        for real in sorted(self.aliases, key=len, reverse=True):
            text = text.replace(real, self.aliases[real])
        for root in sorted(self.roots, key=len, reverse=True):
            text = text.replace(root, '<runtime>')
        text = ISO_TIME.sub('<timestamp>', text)
        text = UUID.sub('<uuid>', text)
        text = text.replace(TODAY, '<date>')
        text = ABS_PATH.sub('<path>', text)
        return text

    def clean(self, value):
        if value is None:
            return None
        obj = json.loads(self.scrub_text(json.dumps(value, ensure_ascii=False)))
        return drop_durations(obj)

    def save(self, name, method, url, request_body, status, response_body, extra=None):
        self.n += 1
        record = {'method': method, 'path': self.scrub_text(url), 'request_body': self.clean(request_body),
                  'status': status, 'response_body': self.clean(response_body)}
        record.update(extra or {})
        target = self.dir / f'{self.n:02d}_{name}.json'
        target.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        return target


def drop_durations(obj):
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            volatile = key.endswith('seconds') or key in ('duration_s', 'elapsed_s')
            out[key] = '<duration>' if volatile and isinstance(value, (int, float)) else drop_durations(value)
        return out
    if isinstance(obj, list):
        return [drop_durations(v) for v in obj]
    return obj


def frames_count(data):
    if not isinstance(data, dict):
        return None
    sim = data.get('sim')
    if not isinstance(sim, dict):
        result = data.get('result')
        sim = result.get('sim') if isinstance(result, dict) else None
    if isinstance(sim, dict) and isinstance(sim.get('frames'), list):
        return len(sim['frames'])
    return None


def parse(response):
    try:
        return response.json()
    except ValueError:
        return response.text


class Steps:
    def __init__(self, sink, client):
        self.sink, self.client = sink, client

    def do(self, name, method, url, body=None, content=None, alias=None, saved_body=None):
        """One request, saved. `alias(data)` runs before saving so ids are placeholders in the file."""
        if content is not None:
            response = self.client.request(method, url, content=content)
            sent = saved_body if saved_body is not None else f'<raw body: {len(content)} bytes>'
        elif body is not None:
            response = self.client.request(method, url, json=body)
            sent = saved_body if saved_body is not None else body
        else:
            response = self.client.request(method, url)
            sent = None
        data = parse(response)
        if alias:
            alias(data)
        extra = {}
        count = frames_count(data)
        if count is not None:
            extra['frames_count'] = count
        self.sink.save(name, method, url, sent, response.status_code, data, extra)
        return response, data

    def export(self, name, url):
        response = self.client.get(url)
        if response.headers.get('content-type', '').startswith('application/zip'):
            entries = sorted(zipfile.ZipFile(io.BytesIO(response.content)).namelist())
            data = {'status': response.status_code, 'content_type': response.headers['content-type'],
                    'zip_entries': entries}
        else:
            data = parse(response)
        self.sink.save(name, 'GET', url, None, response.status_code, data)

    def wait(self, job_id):
        deadline = time.monotonic() + JOB_TIMEOUT_S
        while True:
            job = self.client.get(f'/api/jobs/{job_id}').json()
            if job['status'] != 'running':
                return job
            if time.monotonic() > deadline:
                raise RuntimeError(f'job {job_id} still running after {JOB_TIMEOUT_S}s')
            time.sleep(0.02)

    def job_start(self, name, url, body=None):
        """POST that starts a job; saved as `name`. Returns the start record (or the error response body)."""
        _, data = self.do(name, 'POST', url, body, alias=lambda d: self.sink.alias(d.get('id'), '<job_id>')
                          if isinstance(d, dict) else None)
        return data

    def job_finish(self, name, start):
        """Waits for a started job and saves its final record as GET /api/jobs/<job_id>."""
        if not isinstance(start, dict) or 'id' not in start:
            return None
        final = self.wait(start['id'])
        self.sink.save(f'{name}_final', 'GET', f'/api/jobs/{start["id"]}', None, 200, final,
                       {'frames_count': frames_count(final)} if frames_count(final) is not None else None)
        return final

    def job(self, name, url, body=None):
        return self.job_finish(name, self.job_start(name, url, body))


def run_main(sink, rt):
    app = create_app(rt, overrides(), download=fake_download)
    with TestClient(app, raise_server_exceptions=False) as client:
        s = Steps(sink, client)
        s.do('health', 'GET', '/api/health')
        s.do('families', 'GET', '/api/families')

        # -- LLM settings and usage (scripted: no calls are recorded in the usage ledger)
        s.do('llm_settings', 'GET', '/api/llm')
        s.do('llm_usage_7d', 'GET', '/api/llm/usage?period=7d')
        s.do('usage_alias_all', 'GET', '/api/usage?period=all')
        s.do('llm_usage_bad_period', 'GET', '/api/llm/usage?period=forever')
        _, profile = s.do('llm_profile_add', 'POST', '/api/llm/profiles',
                          {'name': 'Fixture profile', 'kind': 'claude_cli', 'model': 'claude-sonnet-5-5',
                           'effort': 'low', 'timeout_s': 240, 'cli_path': ''},
                          alias=lambda d: sink.alias(d.get('id'), '<profile_id>'))
        s.do('llm_profile_add_bad_provider', 'POST', '/api/llm/profiles',
             {'name': 'Bad', 'kind': 'openai', 'model': 'gpt-x'})
        s.do('llm_profile_edit', 'PUT', f'/api/llm/profiles/{profile["id"]}',
             {'name': 'Fixture profile', 'kind': 'claude_cli', 'model': 'claude-opus-5-5', 'effort': 'medium',
              'timeout_s': 300, 'cli_path': ''})
        s.do('llm_roles_set', 'PUT', '/api/llm/roles', {'roles': {'analysis': 'claude_sonnet'}})
        s.do('llm_key_save', 'PUT', '/api/llm/keys/gemini', {'key': 'fixture-not-a-real-key'},
             saved_body={'key': '<api_key>'})
        s.do('llm_key_delete', 'DELETE', '/api/llm/keys/gemini')
        s.do('llm_profile_delete', 'DELETE', f'/api/llm/profiles/{profile["id"]}')
        s.do('llm_usage_reset', 'POST', '/api/llm/usage/reset')

        # -- reference material (its own project, so the main flow's inputs stay clean)
        _, ref_project = s.do('reference_project_create', 'POST', '/api/projects', {'brief': 'x'},
                              alias=lambda d: sink.alias(d.get('id'), '<project_id>'))
        ref = ref_project['id']
        s.do('reference_upload_exe_rejected', 'POST', f'/api/projects/{ref}/reference/files?filename=a.exe',
             content=b'MZ')
        s.do('reference_upload_png', 'POST', f'/api/projects/{ref}/reference/files?filename=photo.png', content=PNG)
        s.do('reference_clear', 'DELETE', f'/api/projects/{ref}/reference')

        # -- main flow
        _, project = s.do('project_create', 'POST', '/api/projects',
                          {'brief': 'Move a 0.3 kg bottle from A to B'},
                          alias=lambda d: sink.alias(d.get('id'), '<project_id>'))
        pid = project['id']
        s.do('projects_list', 'GET', '/api/projects')
        s.do('project_get_new', 'GET', f'/api/projects/{pid}')
        s.do('reference_text_put', 'PUT', f'/api/projects/{pid}/reference', {'text': 'A bench-top task.'})

        # design step 1: questions. A second design request while the first runs gets 409.
        first = s.job_start('design_questions', f'/api/projects/{pid}/design', {'answers': {}})
        s.do('design_while_running_409', 'POST', f'/api/projects/{pid}/design', {'answers': {}})
        s.job_finish('design_questions', first)
        s.do('project_get_questions', 'GET', f'/api/projects/{pid}')

        # design step 2: designed v1
        s.job('design_answers_designed_v1', f'/api/projects/{pid}/design', {'answers': {'cycle': 6}})
        s.do('version_1_get', 'GET', f'/api/projects/{pid}/versions/1')
        s.do('project_get_designed', 'GET', f'/api/projects/{pid}')

        # revise -> v2, v1 unchanged
        s.job('revise_v2', f'/api/projects/{pid}/revise', {'request': 'make it reach 20 cm further'})
        s.do('version_1_after_revise', 'GET', f'/api/projects/{pid}/versions/1')
        s.do('version_2_get', 'GET', f'/api/projects/{pid}/versions/2')
        s.do('version_99_404', 'GET', f'/api/projects/{pid}/versions/99')

        # approve (frozen afterwards)
        s.do('approve_v2', 'POST', f'/api/projects/{pid}/approve', {'version': 2})
        s.do('approve_v2_again_noop', 'POST', f'/api/projects/{pid}/approve', {'version': 2})
        s.do('approve_v1_conflict_409', 'POST', f'/api/projects/{pid}/approve', {'version': 1})
        s.do('revise_after_approve_409', 'POST', f'/api/projects/{pid}/revise', {'request': 'make it reach 20 cm further'})
        s.do('version_2_approved', 'GET', f'/api/projects/{pid}/versions/2')

        # catalog search and analysis
        s.job('search', f'/api/projects/{pid}/search')
        s.do('candidates', 'GET', f'/api/projects/{pid}/candidates')
        s.job('analyse_acme_ra_6', f'/api/projects/{pid}/candidates/acme_ra_6/analyse',
              {'datasheet_url': 'https://acme.example/ra6.md'})
        s.do('analysis_get', 'GET', f'/api/projects/{pid}/analysis/acme_ra_6')
        s.do('analyse_unknown_candidate', 'POST', f'/api/projects/{pid}/candidates/nope/analyse', {})
        s.do('project_get_analysed', 'GET', f'/api/projects/{pid}')

        # decision and export
        s.do('decision_bad_action_422', 'POST', f'/api/projects/{pid}/decision',
             {'candidate_id': 'acme_ra_6', 'action': 'delete'})
        s.do('decision_approve', 'POST', f'/api/projects/{pid}/decision',
             {'candidate_id': 'acme_ra_6', 'action': 'approve'})
        s.export('export_zip', f'/api/projects/{pid}/export')

        # generic errors
        s.do('project_unknown_404', 'GET', '/api/projects/p_0000000000')
        s.do('project_bad_body_422', 'POST', '/api/projects', {})
        s.do('project_path_traversal', 'GET', '/api/projects/..%2F..%2Fx')
        s.do('job_unknown_404', 'GET', '/api/jobs/000000000000')
        s.do('api_unknown_route_404', 'GET', '/api/does-not-exist')

        s.do('project_delete', 'DELETE', f'/api/projects/{pid}')
        s.do('project_get_after_delete_404', 'GET', f'/api/projects/{pid}')
        s.do('reference_project_delete', 'DELETE', f'/api/projects/{ref}')


def run_ssrf(sink, rt):
    """Real safe_download for the datasheet URL check (vendor host served from the fixture)."""
    app = create_app(rt, overrides(), download=mixed_download)
    with TestClient(app, raise_server_exceptions=False) as client:
        s = Steps(sink, client)
        _, empty = s.do('project_no_design_create', 'POST', '/api/projects', {'brief': 'x'},
                        alias=lambda d: sink.alias(d.get('id'), '<project_id>'))
        s.job('search_before_approval', f'/api/projects/{empty["id"]}/search')
        s.do('export_before_design_422', 'GET', f'/api/projects/{empty["id"]}/export')

        _, project = s.do('ssrf_project_create', 'POST', '/api/projects', {'brief': 'x'},
                          alias=lambda d: sink.alias(d.get('id'), '<project_id>'))
        pid = project['id']
        s.job('ssrf_design_questions', f'/api/projects/{pid}/design', {'answers': {}})
        s.job('ssrf_design_answers', f'/api/projects/{pid}/design', {'answers': {'cycle': 6}})
        s.do('ssrf_approve_v1', 'POST', f'/api/projects/{pid}/approve', {'version': 1})
        s.job('ssrf_search', f'/api/projects/{pid}/search')
        s.job('ssrf_analyse_http_127_rejected', f'/api/projects/{pid}/candidates/acme_ra_6/analyse',
              {'datasheet_url': 'http://127.0.0.1/secret.pdf'})
        s.do('ssrf_upload_datasheet', 'POST',
             f'/api/projects/{pid}/candidates/acme_ra_6/datasheet?filename=ra6.md',
             content=DATASHEET.encode())
        s.job('ssrf_analyse_uploaded_datasheet', f'/api/projects/{pid}/candidates/acme_ra_6/analyse')


def main():
    if GOLDEN_DIR.exists():
        for old in GOLDEN_DIR.glob('[0-9][0-9]_*.json'):
            old.unlink()
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    rt_main = Path(tempfile.mkdtemp(prefix='app_main_', dir=SCRATCH))
    rt_ssrf = Path(tempfile.mkdtemp(prefix='app_ssrf_', dir=SCRATCH))
    sink = Sink(GOLDEN_DIR)
    sink.roots |= {str(rt_main), str(rt_main.resolve()), str(rt_ssrf), str(rt_ssrf.resolve()),
                   str(SCRATCH), str(SCRATCH.resolve()), str(HERE.resolve())}
    try:
        run_main(sink, rt_main)
        run_ssrf(sink, rt_ssrf)
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)
    print(f'wrote {sink.n} files to {GOLDEN_DIR}')


if __name__ == '__main__':
    main()
