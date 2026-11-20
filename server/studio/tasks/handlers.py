"""The task implementations. Each takes (request: dict, ctx: Context) and returns the result data (a JSON-able dict).
They call the pure functions of studio.pipeline and never touch runtime/ (only ctx.out_dir)."""
import asyncio
import base64
import time
from dataclasses import dataclass
from pathlib import Path

from .. import families
from ..catalog.fetch import safe_download
from ..ingest import images as image_intake
from ..ingest import urdf as urdf_intake
from ..ingest.docs import extract_text
from ..llm import gemini as gemini_module
from ..llm.base import Image, supports_images
from ..llm.claude_cli import MODELS
from ..llm.registry import DEFAULT_ROLES, PROVIDERS, ROLES, default_profiles
from ..pipeline import catalog as catalog_pipeline
from ..pipeline import design as design_pipeline
from ..pipeline import fleet as fleet_pipeline
from ..pipeline import studio as studio_pipeline
from ..schemas import Candidate, DesignVersion, StudioBuild, TechnicalSpec, TwinMoveRequest
from .llms import TaskLLMs

TEST_PNG = ('iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAKklEQVR42u3NsQkAAAzDsPz/dHpDxoLAo0FpMjXuDQAAAAAAAAAAAPgCHDaH/C4jlqxWAAAAAElFTkSuQmCC')


@dataclass
class Context:
    llms: TaskLLMs
    out_dir: Path
    progress: callable
    profile: dict | None = None        # llm.profile, for llm_test
    context: dict | None = None
    download: callable = safe_download


def version_json(v: DesignVersion) -> dict:
    """A version as JSON; the optional repair and fleet keys stay absent when unset (older versions never had them)."""
    out = v.model_dump(mode='json')
    for key in ('repair', 'fleet'):
        if out.get(key) is None:
            out.pop(key, None)
    return out


def _version(data, what='version'):
    if not isinstance(data, dict):
        raise ValueError(f'input.{what} is required')
    return DesignVersion.model_validate(data)


def _auto_repair(inp) -> bool:
    """Input `auto_repair` (bool); true when absent."""
    value = inp.get('auto_repair')
    return True if value is None else bool(value)


def _parsed(urdf):
    return urdf_intake.parse_urdf(urdf.encode())


def agent_reference(reference):
    """Input `reference` ({text, files: [{path, kind}]}) -> the agent's view {'text', 'urdf', 'images'} or None."""
    if not reference:
        return None
    images, urdf = [], None
    for f in reference.get('files') or []:
        path = Path(f['path'])
        payload = path.read_bytes()
        if f.get('kind') == 'urdf':
            urdf = urdf_intake.summary(urdf_intake.parse_urdf(urdf_payload(payload, path.name)))
        elif f.get('kind') == 'image':
            images.append(Image(base64.b64encode(payload).decode(), image_intake.image_type(payload) or 'image/jpeg', path.name))
        else:
            raise ValueError(f'Unknown reference file kind {f.get("kind")!r}')
    text = reference.get('text') or ''
    if not (text or images or urdf):
        return None
    return {'text': text, 'urdf': urdf, 'images': images}


def answer_images(items):
    """Input `answer_images` ([{path, question}]) -> [Image] labelled 'answer:<question>:<filename>'."""
    images = []
    for item in items or []:
        path = Path(item['path'])
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise ValueError(f'Cannot read the answer image {path.name}: {error.strerror or error}') from error
        images.append(Image(base64.b64encode(payload).decode(), image_intake.image_type(payload) or 'image/jpeg',
                            f'answer:{item.get("question") or "_general"}:{path.name}'))
    return images


def brief_images(items):
    """Input `brief_images` ([{path, name}]) -> [Image] labelled 'brief:<name>'."""
    images = []
    for item in items or []:
        path = Path(item['path'])
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise ValueError(f'Cannot read the brief image {path.name}: {error.strerror or error}') from error
        images.append(Image(base64.b64encode(payload).decode(), image_intake.image_type(payload) or 'image/jpeg',
                            f'brief:{item.get("name") or path.name}'))
    return images


def _answer_extras(inp):
    """Input `comments`, `notes`, `answer_images`, `brief_images` -> extras for the spec round (only non-empty parts) or None."""
    extras = {'comments': inp.get('comments') or None, 'notes': inp.get('notes') or None,
              'answer_images': answer_images(inp.get('answer_images')) or None,
              'brief_images': brief_images(inp.get('brief_images')) or None}
    return {k: v for k, v in extras.items() if v} or None


def urdf_payload(payload, name):
    """The URDF bytes of an uploaded .urdf or .zip file."""
    if name.lower().endswith('.zip'):
        _, path, files = urdf_intake.archive_files(payload, name)
        return files[path]
    return payload


# -- tasks ---------------------------------------------------------------------------------------------------------------

async def meta(inp, ctx):
    return {'families': families.describe(), 'kinds': sorted(PROVIDERS), 'roles': list(ROLES),
            'models': {'claude_cli': list(MODELS), 'gemini': list(gemini_module.MODELS)},
            'default_roles': dict(DEFAULT_ROLES), 'default_profiles': default_profiles()}


async def design(inp, ctx):
    """One spec round and, when the spec is complete, the whole design. `round` is the number of spec rounds finished
    before this call (project.json's round); `answers` are all answers so far (Java merges). An optional `spec` skips
    the spec round (design retry)."""
    progress = ctx.progress
    round_ = int(inp.get('round') or 0) + 1
    hint = None
    if inp.get('spec'):
        spec = TechnicalSpec.model_validate(inp['spec'])
    else:
        brief = (inp.get('brief') or '').strip()
        if not brief:
            raise ValueError('Describe the job first')
        step = await design_pipeline.specify(ctx.llms, brief, agent_reference(inp.get('reference')), inp.get('answers') or {},
                                             round_, progress, _answer_extras(inp))
        if step['status'] == 'questions':
            return {'status': 'questions', 'questions': step['questions'], 'round': round_}
        spec, hint = step['spec'], step.get('fleet')
    version = await design_pipeline.design(ctx.llms, spec, progress, auto_repair=_auto_repair(inp))
    out = {'status': 'designed', 'version': version_json(version), 'parsed': _parsed(version.urdf), 'round': round_}
    advice = design_pipeline.fleet_advice(version, hint, progress)
    if advice:                                   # only when flagged: the single-robot result is otherwise unchanged
        out['fleet_advice'] = advice
    return out


async def revise(inp, ctx):
    base = _version(inp.get('base'), 'base')
    version = await design_pipeline.revise(ctx.llms, base, inp.get('request'), inp.get('t'), ctx.progress,
                                              auto_repair=_auto_repair(inp))
    out = {'version': version_json(version), 'parsed': _parsed(version.urdf)}
    if version.fleet is not None:
        out['fleet_parsed'] = fleet_pipeline.fleet_parsed(version)
    else:
        advice = design_pipeline.fleet_advice(version, None, ctx.progress)
        if advice:
            out['fleet_advice'] = advice
    return out


async def fleet_assign(inp, ctx):
    """No LLM: the automatic or the user's (validated) assignment of a version's task to several robots, with a
    layout preview. An assignment that does not fit the task is a value error."""
    version = _version(inp.get('version'))
    return await asyncio.to_thread(fleet_pipeline.assign, version, inp.get('limits'), inp.get('assignment'))


async def fleet_design(inp, ctx):
    """A multi-robot version of `base`'s task for the user's limits and assignment (null = automatic)."""
    base = _version(inp.get('base'), 'base')
    version = await fleet_pipeline.design_fleet(ctx.llms, base, inp.get('limits'), inp.get('assignment'), ctx.progress,
                                                primary=inp.get('primary'), trigger=inp.get('trigger'),
                                                auto_repair=_auto_repair(inp))
    return {'version': version_json(version), 'parsed': _parsed(version.urdf),
            'fleet_parsed': fleet_pipeline.fleet_parsed(version)}


async def find(inp, ctx):
    version = _version(inp.get('version'))
    result = await catalog_pipeline.find(ctx.llms, version, ctx.progress, ctx.download, limit=int(inp.get('limit') or 6),
                                         member=inp.get('member'))
    return {'candidates': [c.model_dump(mode='json') for c in result['candidates']], 'dropped': result['dropped']}


async def analyse(inp, ctx):
    version = _version(inp.get('version'))
    candidate = Candidate.model_validate(inp.get('candidate'))
    stored, source = None, ''
    if inp.get('document_path'):
        document_file = Path(inp['document_path'])   # an uploaded PDF/HTML/text file or a saved .txt: extract text from any
        stored = extract_text(document_file.read_bytes(), document_file.name)
        source = inp.get('document_source') or 'uploaded document'
    result = await catalog_pipeline.analyse(ctx.llms, version, candidate, inp.get('datasheet_url'), ctx.progress, ctx.download,
                                            stored=stored, stored_source=source, member=inp.get('member'))
    analysis = result['analysis']
    document = result['document']
    if document:
        (ctx.out_dir / 'document.txt').write_text(document['text'], encoding='utf-8')
        document = {'file': 'document.txt', 'url': document['url'], 'title': document['title']}
    return {'analysis': analysis.model_dump(mode='json'), 'parsed': _parsed(analysis.urdf), 'document': document}


async def inspect_upload(inp, ctx):
    path, kind = Path(inp.get('path') or ''), inp.get('kind')
    if not path.is_file():
        raise FileNotFoundError('Uploaded file not found')
    payload = path.read_bytes()
    if kind == 'urdf':
        return {'ok': True, 'summary': urdf_intake.summary(urdf_intake.parse_urdf(urdf_payload(payload, path.name)))}
    if kind == 'datasheet':
        return {'ok': True, 'text_chars': len(extract_text(payload, path.name))}
    if kind == 'image':
        return {'ok': True, 'mime': image_intake.check_image(payload)}
    raise ValueError(f'Unknown upload kind {kind!r}; use urdf, datasheet or image')


async def export_assets(inp, ctx):
    urdf, want = inp.get('urdf'), inp.get('want') or []
    if not isinstance(urdf, str) or not urdf.strip():
        raise ValueError('input.urdf is required')
    unknown = [w for w in want if w not in ('parsed', 'glb', 'stl')]
    if unknown:
        raise ValueError(f'Unknown export asset {unknown[0]!r}; use parsed, glb or stl')
    result, files = {}, []
    if 'parsed' in want:
        result['parsed'] = _parsed(urdf)
    if 'glb' in want or 'stl' in want:
        from ..families import export
        if 'glb' in want:
            ctx.progress('Writing design.glb')
            (ctx.out_dir / 'design.glb').write_bytes(await asyncio.to_thread(export.meshes, urdf))
            files.append('design.glb')
        if 'stl' in want:
            ctx.progress('Writing design.stl')
            (ctx.out_dir / 'design.stl').write_bytes(await asyncio.to_thread(export.stl, urdf))
            files.append('design.stl')
    result['files'] = files
    return result


async def llm_test(inp, ctx):
    if not ctx.profile:
        raise ValueError('llm.profile is required for llm_test')
    adapter = ctx.llms.build(ctx.profile, 'test')
    started = time.monotonic()
    ctx.progress('Text call')
    text = await adapter.invoke('Reply with the single word: ready', 'You are a connection test. Follow the instruction exactly.')
    seconds = round(time.monotonic() - started, 1)
    # the old FastAPI shape (text, text_seconds, images) is what the client Settings page reads; ok/answer/seconds are kept
    result = {'text': text.strip()[:200], 'text_seconds': seconds, 'images': None, 'ok': True, 'answer': text.strip()[:200],
              'seconds': seconds}
    if supports_images(adapter):
        ctx.progress('Image call')
        started = time.monotonic()
        answer = await adapter.invoke_multimodal('What two colours does this image show, left then right? Five words at most.',
                                                 'You are a connection test.', [Image(TEST_PNG, 'image/png', 'test pattern')])
        result.update(images=answer.strip()[:200], image_seconds=round(time.monotonic() - started, 1))
    return result


# -- robot studio ----------------------------------------------------------------------------------------------------

def _studio_build(data, what='build'):
    if not isinstance(data, dict):
        raise ValueError(f'input.{what} is required')
    return StudioBuild.model_validate(data)


async def studio_build(inp, ctx):
    """Size, source, style and CAD an approved design (see pipeline/studio.py). Files go to out_dir."""
    version = _version(inp.get('version'))
    base = _studio_build(inp['base_build'], 'base_build') if inp.get('base_build') else None
    number = inp.get('build')
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise ValueError('input.build must be a positive integer')
    pinned = inp.get('pinned') or {}
    if not isinstance(pinned, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in pinned.items()):
        raise ValueError('input.pinned must be an object {joint: alternative_id}')
    result = await studio_pipeline.build(ctx.llms, version, inp.get('member'), base, str(inp.get('request') or ''), pinned,
                                         number, ctx.out_dir, ctx.progress, download=ctx.download)
    return {'build': result.model_dump(mode='json'), 'parsed': _parsed(result.urdf)}


async def studio_twin_move(inp, ctx):
    """One twin move under the build's limits. Needs the design version (the twin rebuilds the kinematic chain)."""
    from ..robot import twin
    build, version = _studio_build(inp.get('build')), _version(inp.get('version'))
    if not isinstance(inp.get('request'), dict):
        raise ValueError('input.request is required')
    req = TwinMoveRequest.model_validate(inp['request'])
    return (await asyncio.to_thread(twin.move, build, version, req)).model_dump(mode='json')


async def studio_run_task(inp, ctx):
    """The design's waypoints replayed under the build's limits."""
    from ..robot import twin
    build, version = _studio_build(inp.get('build')), _version(inp.get('version'))
    scale = inp.get('speed_scale', 1.0)
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 0 < scale <= 2.0:
        raise ValueError('input.speed_scale must be a number above 0 and at most 2')
    return (await asyncio.to_thread(twin.run_task, build, version, float(scale))).model_dump(mode='json')


async def studio_pdf(inp, ctx):
    """manufacturer.pdf, bom.csv and package.zip into out_dir. `build_dir` (read only) holds the CAD files; they are
    staged with bom.csv in out_dir/_stage so the package holds bom.csv too, and the stage is removed afterwards."""
    import shutil

    from ..robot import report
    build, version = _studio_build(inp.get('build')), _version(inp.get('version'))
    project, build_dir = inp.get('project') or {}, Path(inp.get('build_dir') or '')
    if not build_dir.is_dir():
        raise FileNotFoundError('input.build_dir is not a folder')
    snapshots = inp.get('snapshots') or []
    out = ctx.out_dir

    def work():
        ctx.progress('Writing the bill of materials')
        bom = report.write_bom_csv(build, out / 'bom.csv')
        ctx.progress('Writing the manufacturer PDF')
        pdf = report.write_pdf(build, version, project, build_dir, snapshots, out / 'manufacturer.pdf')
        ctx.progress('Writing the build package')
        stage = out / '_stage'
        shutil.rmtree(stage, ignore_errors=True)
        try:
            stage.mkdir()
            for name in ('assembly.step', 'parts', 'urdf'):
                src = build_dir / name
                if src.is_dir():
                    shutil.copytree(src, stage / name)
                elif src.is_file():
                    shutil.copy2(src, stage / name)
            shutil.copy2(out / 'bom.csv', stage / 'bom.csv')
            package = report.write_package(stage, out / 'manufacturer.pdf', out / 'package.zip')
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return pdf, bom, package

    pdf, bom, package = await asyncio.to_thread(work)
    return {'pdf': 'manufacturer.pdf', 'package': 'package.zip',
            'files': [f.model_dump(mode='json') for f in (pdf, bom, package)]}


HANDLERS = {'meta': meta, 'design': design, 'revise': revise, 'find': find, 'analyse': analyse,
            'inspect_upload': inspect_upload, 'export_assets': export_assets, 'llm_test': llm_test,
            'fleet_assign': fleet_assign, 'fleet_design': fleet_design, 'studio_build': studio_build,
            'studio_twin_move': studio_twin_move, 'studio_run_task': studio_run_task, 'studio_pdf': studio_pdf}
