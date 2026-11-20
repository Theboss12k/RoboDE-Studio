"""HTTP API (FastAPI, 127.0.0.1:8800). Long work runs as jobs: POST returns the job record, poll GET /api/jobs/{id}.
Endpoints are listed in docs/CONTRACTS.md.
"""
import asyncio
import io
import json
import time
import traceback
import uuid
import zipfile
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from .. import families
from ..catalog.fetch import safe_download
from ..config import RUNTIME, STUDIO_ROOT
from ..ingest.docs import extract_text
from ..ingest.urdf import parse_urdf
from ..llm.base import Image, LLMError, supports_images
from ..llm.registry import LLMRegistry, Profile
from ..llm.usage import set_context
from ..pipeline import catalog as catalog_pipeline
from ..pipeline import design as design_pipeline
from ..projects import ProjectStore
from ..util import now, safe_name

MAX_BODY = 64 * 1024 * 1024
TEST_PNG = ('iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAKklEQVR42u3NsQkAAAzDsPz/dHpDxoLAo0FpMjXuDQAAAAAAAAAAAPgCHDaH/C4jlqxWAAAAAElFTkSuQmCC')

README = """RoboDE Studio design export

What this is: a rough robot concept for the task in spec.json, with its requirements and the simulated task.
design.urdf and design.glb show the robot in its zero pose.

Limits you should know:
- Checks are kinematic and static only (reach, joint limits, static joint torques, speed, cycle time, coarse
  collisions). There is no dynamics, no contact simulation and no controller.
- Nothing here was tested on hardware.
- Requirement numbers come from the code in physics/ and the robot families; the AI chose the task layout and the
  robot family. Check the assumptions in spec.json.
- Prices of real robots are web estimates with a source and a date; they change.
- Manufacturer values are marked verified only when their quote was found in the datasheet text.
"""


class ApiKey(BaseModel):
    key: str = Field(min_length=1, max_length=400)


class Roles(BaseModel):
    roles: dict[str, str]


class NewProject(BaseModel):
    brief: str = Field(min_length=1, max_length=8000)


class ReferenceText(BaseModel):
    text: str = Field(default='', max_length=60000)


class Design(BaseModel):
    answers: dict = {}


class Revise(BaseModel):
    request: str = Field(min_length=1, max_length=2000)


class Approve(BaseModel):
    version: int


class Analyse(BaseModel):
    datasheet_url: str | None = Field(default=None, max_length=800)


class Decision(BaseModel):
    candidate_id: str
    action: Literal['approve', 'robot_studio']


def create_app(runtime=RUNTIME, llm_overrides=None, download=None):
    app = FastAPI(title='RoboDE Studio')
    runtime = Path(runtime)
    store = ProjectStore(runtime)
    llms = LLMRegistry(runtime, llm_overrides)
    download = download or safe_download
    jobs = {}
    app.state.store, app.state.llms, app.state.jobs = store, llms, jobs

    @app.middleware('http')
    async def limit_body(request: Request, call_next):
        length = request.headers.get('content-length')
        if length and length.isdigit() and int(length) > MAX_BODY:
            return JSONResponse({'detail': 'The request body is too large (64 MB at most)'}, status_code=413)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def value_error(request, error):
        return JSONResponse({'detail': str(error)}, status_code=422)

    @app.exception_handler(FileNotFoundError)
    async def not_found(request, error):
        return JSONResponse({'detail': str(error) or 'Not found'}, status_code=404)

    @app.exception_handler(LLMError)
    async def llm_error(request, error):
        return JSONResponse({'detail': str(error)}, status_code=502)

    # -- jobs ---------------------------------------------------------------------------------------------------
    def job(kind, project_id, work):
        """Run `work(progress)` in the background; returns the job record."""
        if kind in ('design', 'revise') and any(j['robot_id'] == project_id and j['status'] == 'running'
                                                and j['kind'] in ('design', 'revise') for j in jobs.values()):
            raise HTTPException(409, 'This project is still working on another design step')
        record = {'id': uuid.uuid4().hex[:12], 'kind': kind, 'robot_id': project_id, 'status': 'running', 'progress': [],
                  'result': None, 'error': None, 'error_code': None, 'started': now()}
        jobs[record['id']] = record

        async def runner():
            set_context(robot_id=project_id, task=kind)  # labels this job's model calls in the usage ledger
            try:
                record['result'] = await work(record['progress'].append)
                record['status'] = 'done'
            except (ValueError, LLMError, FileNotFoundError) as error:
                record['status'], record['error'] = 'error', str(error)
                record['error_code'] = getattr(error, 'code', None)
            except Exception as error:  # keep the server alive; report the failure
                record['status'], record['error'] = 'error', f'{error.__class__.__name__}: {error}'
                traceback.print_exc()
        record['task'] = asyncio.create_task(runner())
        return public(record)

    def public(record):
        return {k: v for k, v in record.items() if k != 'task'}

    @app.get('/api/jobs/{job_id}')
    async def get_job(job_id: str):
        if job_id not in jobs:
            raise HTTPException(404, 'Job not found')
        return public(jobs[job_id])

    # -- health, families, LLM settings (same bodies as the Testbed) -------------------------------------------
    @app.get('/api/health')
    async def health():
        return {'ok': True, 'llm': llms.data()['roles']}

    @app.get('/api/families')
    async def list_families():
        return families.describe()

    @app.get('/api/llm')
    async def llm_settings():
        return llms.describe()

    @app.post('/api/llm/profiles')
    async def add_profile(profile: Profile):
        return llms.upsert(profile)

    @app.put('/api/llm/profiles/{profile_id}')
    async def edit_profile(profile_id: str, profile: Profile):
        llms.profile(profile_id)
        return llms.upsert(profile, profile_id)

    @app.delete('/api/llm/profiles/{profile_id}')
    async def delete_profile(profile_id: str):
        llms.delete(profile_id)
        return llms.describe()

    @app.put('/api/llm/roles')
    async def set_roles(body: Roles):
        return llms.set_roles(body.roles)

    @app.get('/api/llm/usage')
    @app.get('/api/usage')
    async def llm_usage(period: str = '7d'):
        if period not in ('today', '7d', '30d', 'all'):
            raise ValueError('period is today, 7d, 30d or all')
        return llms.usage.summary(period)

    @app.post('/api/llm/usage/reset')
    async def llm_usage_reset():
        llms.usage.reset()
        return llms.usage.summary('all')

    @app.put('/api/llm/keys/{provider}')
    async def save_key(provider: str, body: ApiKey):
        llms.keys.set(provider, body.key)  # stored with mode 0600; never returned
        return llms.keys.status()

    @app.delete('/api/llm/keys/{provider}')
    async def delete_key(provider: str):
        llms.keys.delete(provider)
        return llms.keys.status()

    @app.post('/api/llm/profiles/{profile_id}/test')
    async def test_profile(profile_id: str):
        adapter = llms.adapter(profile_id)

        async def work(progress):
            started = time.monotonic()
            progress('Text call')
            text = await adapter.invoke('Reply with the single word: ready', 'You are a connection test. Follow the instruction exactly.')
            result = {'text': text.strip()[:200], 'text_seconds': round(time.monotonic() - started, 1), 'images': None}
            if supports_images(adapter):
                progress('Image call')
                started = time.monotonic()
                answer = await adapter.invoke_multimodal('What two colours does this image show, left then right? Five words at most.',
                                                         'You are a connection test.', [Image(TEST_PNG, 'image/png', 'test pattern')])
                result.update(images=answer.strip()[:200], image_seconds=round(time.monotonic() - started, 1))
            return result
        return job('llm_test', None, work)

    # -- projects -----------------------------------------------------------------------------------------------
    @app.get('/api/projects')
    async def list_projects():
        return store.list()

    @app.post('/api/projects')
    async def create_project(body: NewProject):
        return store.create(body.brief).view()

    @app.get('/api/projects/{pid}')
    async def get_project(pid: str):
        return store.open(pid).view()

    @app.delete('/api/projects/{pid}')
    async def delete_project(pid: str):
        store.delete(pid)
        return {'ok': True}

    @app.put('/api/projects/{pid}/reference')
    async def put_reference(pid: str, body: ReferenceText):
        project = store.open(pid)
        project.set_reference_text(body.text)
        return project.view()

    async def raw_body(request, filename):
        payload = await request.body()
        if len(payload) > MAX_BODY:
            raise HTTPException(413, 'The upload is too large (64 MB at most)')
        return safe_name(filename), payload

    @app.post('/api/projects/{pid}/reference/files')
    async def upload_reference(pid: str, request: Request, filename: str):
        project = store.open(pid)
        name, payload = await raw_body(request, filename)
        project.add_reference_file(name, payload)
        return project.view()

    @app.delete('/api/projects/{pid}/reference')
    async def clear_reference(pid: str):
        project = store.open(pid)
        project.clear_reference()
        return project.view()

    # -- design -------------------------------------------------------------------------------------------------
    @app.post('/api/projects/{pid}/design')
    async def design(pid: str, body: Design):
        project = store.open(pid)
        return job('design', pid, lambda progress: design_pipeline.specify(llms, project, body.answers, progress))

    def version_view(version):
        return {**version.model_dump(mode='json'), 'parsed': parse_urdf(version.urdf.encode())}

    @app.get('/api/projects/{pid}/versions/{n}')
    async def get_version(pid: str, n: int):
        return version_view(store.open(pid).version(n))

    @app.post('/api/projects/{pid}/revise')
    async def revise(pid: str, body: Revise):
        project = store.open(pid)

        async def work(progress):
            version = await design_pipeline.revise(llms, project, body.request, progress)
            return {'version': version.version}
        return job('revise', pid, work)

    @app.post('/api/projects/{pid}/approve')
    async def approve(pid: str, body: Approve):
        project = store.open(pid)
        project.approve(body.version)
        return project.view()

    # -- catalog ------------------------------------------------------------------------------------------------
    @app.post('/api/projects/{pid}/search')
    async def search(pid: str):
        project = store.open(pid)
        return job('search', pid, lambda progress: catalog_pipeline.find(llms, project, progress))

    @app.get('/api/projects/{pid}/candidates')
    async def candidates(pid: str):
        return [c.model_dump(mode='json') for c in store.open(pid).candidates()]

    def analysis_view(analysis):
        return {**analysis.model_dump(mode='json'), 'parsed': parse_urdf(analysis.urdf.encode())}

    @app.post('/api/projects/{pid}/candidates/{cid}/analyse')
    async def analyse(pid: str, cid: str, body: Analyse | None = None):
        project = store.open(pid)
        project.candidate(cid)

        async def work(progress):
            analysis = await catalog_pipeline.analyse(llms, project, cid, (body.datasheet_url if body else None), progress, download)
            return analysis_view(analysis)
        return job('analyse', pid, work)

    @app.post('/api/projects/{pid}/candidates/{cid}/datasheet')
    async def upload_datasheet(pid: str, cid: str, request: Request, filename: str):
        project = store.open(pid)
        project.candidate(cid)
        name, payload = await raw_body(request, filename)
        text = extract_text(payload, name)
        project.save_document(cid, text, f'uploaded document {name}')
        return {'ok': True, 'characters': len(text)}

    @app.get('/api/projects/{pid}/analysis/{cid}')
    async def get_analysis(pid: str, cid: str):
        return analysis_view(store.open(pid).analysis(cid))

    @app.post('/api/projects/{pid}/decision')
    async def decision(pid: str, body: Decision):
        project = store.open(pid)
        project.set_decision(body.candidate_id, body.action)
        return project.view()

    # -- export -------------------------------------------------------------------------------------------------
    @app.get('/api/projects/{pid}/export')
    async def export(pid: str):
        project = store.open(pid)
        version = project.approved() or project.latest()
        if version is None:
            raise ValueError('There is no design to export yet')
        sim = version.sim
        files = {'design.urdf': version.urdf,
                 'spec.json': version.spec.model_dump_json(indent=2),
                 'requirements.json': json.dumps([r.model_dump(mode='json') for r in version.requirements], indent=2),
                 'scene.json': version.scene.model_dump_json(indent=2),
                 'sim_summary.json': json.dumps({'version': version.version, 'approved': version.approved, 'ok': sim.ok,
                                                 'duration_s': sim.duration_s, 'robot': {'family': version.robot.family,
                                                 'name': version.robot.name, 'params': version.robot.params},
                                                 'checks': [c.model_dump(mode='json') for c in sim.checks],
                                                 'events': [e.model_dump(mode='json') for e in sim.events]}, indent=2),
                 'README.txt': README}
        try:
            from ..families.export import meshes
            files['design.glb'] = await asyncio.to_thread(meshes, version.urdf)
        except Exception as error:  # the export is still useful without the mesh
            files['README.txt'] += f'\ndesign.glb could not be written: {error}\n'
        for cid in project.analyses():
            analysis = project.analysis(cid)
            files[f'candidates/{cid}/analysis.json'] = analysis.model_dump_json(indent=2, exclude={'urdf': True, 'sim': {'frames'}})
            files[f'candidates/{cid}/manufacturer_values.json'] = analysis.manufacturer_spec.model_dump_json(indent=2)
            files[f'candidates/{cid}/candidate.urdf'] = analysis.urdf
        decision = project.meta.get('decision')
        if decision:
            files['decision.json'] = json.dumps(decision, indent=2)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        return Response(buffer.getvalue(), media_type='application/zip',
                        headers={'Content-Disposition': f'attachment; filename="{pid}-v{version.version}.zip"'})

    # -- the built client ---------------------------------------------------------------------------------------
    dist = STUDIO_ROOT / 'client' / 'dist'

    @app.get('/{path:path}', include_in_schema=False)
    async def client(path: str):
        if path.startswith('api/'):
            raise HTTPException(404, 'Not found')
        file = (dist / path).resolve()
        if path and dist.resolve() in file.parents and file.is_file():
            return FileResponse(file)
        if (dist / 'index.html').exists():
            return FileResponse(dist / 'index.html')
        return PlainTextResponse('RoboDE Studio API is running. Start the client with: npm run dev')

    return app
