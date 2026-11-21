"""The task implementations. Each takes (request: dict, ctx: Context) and returns the result data (a JSON-able dict).
They call the pure functions of studio.pipeline and never touch runtime/ (only ctx.out_dir)."""
import asyncio
import base64
import time
from dataclasses import dataclass
from pathlib import Path

from .. import families
from ..ar import geometry as ar_geometry
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
from ..schemas import ArCalibration, ArIntrinsics, ArReference, Candidate, DesignVersion, TechnicalSpec
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


def _answer_extras(inp):
    """Input `comments`, `notes`, `answer_images` -> extras for the spec round (only non-empty parts) or None."""
    extras = {'comments': inp.get('comments') or None, 'notes': inp.get('notes') or None,
              'answer_images': answer_images(inp.get('answer_images')) or None}
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
        spec = step['spec']
    version = await design_pipeline.design(ctx.llms, spec, progress, auto_repair=_auto_repair(inp))
    return {'status': 'designed', 'version': version_json(version), 'parsed': _parsed(version.urdf), 'round': round_}


async def revise(inp, ctx):
    base = _version(inp.get('base'), 'base')
    version = await design_pipeline.revise(ctx.llms, base, inp.get('request'), inp.get('t'), ctx.progress,
                                              auto_repair=_auto_repair(inp))
    return {'version': version_json(version), 'parsed': _parsed(version.urdf)}


async def find(inp, ctx):
    version = _version(inp.get('version'))
    result = await catalog_pipeline.find(ctx.llms, version, ctx.progress, ctx.download, limit=int(inp.get('limit') or 6))
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
                                            stored=stored, stored_source=source)
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


def _ar_photo(inp) -> Path:
    """Input `image.path` -> the photo path, after the intake check (size and PNG/JPEG/WebP signature)."""
    image = inp.get('image')
    path = Path((image or {}).get('path') or '') if isinstance(image, dict) else Path('')
    if not path.is_file():
        raise FileNotFoundError('The photo was not found')
    image_intake.check_image(path.read_bytes())
    return path


async def ar_calibrate(inp, ctx):
    """Camera and floor from a photographed reference rectangle (docs/AR_PHOTO.md)."""
    path = _ar_photo(inp)
    reference = ArReference.model_validate(inp.get('reference'))
    intrinsics = ArIntrinsics.model_validate(inp['intrinsics']) if inp.get('intrinsics') else None
    width, height = ar_geometry.image_size(path)
    ctx.progress('Calibrating the camera')
    result = ar_geometry.calibrate(width, height, reference.corners_px, reference.size_m,
                                   intrinsics.model_dump() if intrinsics else None)
    return ArCalibration.model_validate(result).model_dump(mode='json')


async def ar_estimate(inp, ctx):
    """Camera and floor from the photo alone, with the optional depth model (torch + MoGe)."""
    path = _ar_photo(inp)
    ctx.progress('Estimating the camera and floor')
    from ..ar import estimate as ar_estimate_module      # lazy: the module itself imports torch only inside estimate()
    result = await asyncio.to_thread(ar_estimate_module.estimate, path)
    return ArCalibration.model_validate(result).model_dump(mode='json')


HANDLERS = {'meta': meta, 'design': design, 'revise': revise, 'find': find, 'analyse': analyse,
            'inspect_upload': inspect_upload, 'export_assets': export_assets, 'llm_test': llm_test,
            'ar_calibrate': ar_calibrate, 'ar_estimate': ar_estimate}


async def apply_scene(inp, ctx):
    """The user's scene edits (docs/SITE_SCAN.md) as a new version, computed in code; no LLM unless auto_repair is set."""
    from ..pipeline import apply_scene as apply_scene_pipeline
    from ..schemas import SceneSpec
    base = _version(inp.get('base'), 'base')
    if not isinstance(inp.get('scene'), dict):
        raise ValueError('input.scene is required')
    scene = SceneSpec.model_validate(inp['scene'])
    markers = inp.get('markers') or []
    if not isinstance(markers, list):
        raise ValueError('input.markers must be a list of {id, position}')
    auto_repair = bool(inp.get('auto_repair'))
    version = await apply_scene_pipeline.apply_scene(base, scene, markers, auto_repair, inp.get('note') or '', ctx.progress,
                                                     ctx.llms if auto_repair else None)
    return {'version': version_json(version), 'parsed': _parsed(version.urdf)}


HANDLERS['apply_scene'] = apply_scene


# -- site scan (docs/SITE_SCAN.md) ----------------------------------------------------------------------------------------

def _scan_unavailable(error):
    from ..scan import ScanError
    return ScanError('scan_backend_unavailable', 'The site scan needs the optional backend (av, OpenCV, SciPy, Open3D, '
                                                 f'torch, MoGe and trimesh); one of them is missing or blocked ({error})')


def _scan_video(inp) -> Path:
    """Input `video.path` -> the video file path."""
    video = inp.get('video')
    path = Path((video or {}).get('path') or '') if isinstance(video, dict) else Path('')
    if not path.is_file():
        raise FileNotFoundError('The video was not found')
    return path


def _scan_out_path(ctx, value, what) -> Path:
    """An output path of the request: relative paths are under the job's output folder; absolute ones must lie in it."""
    if not value:
        raise ValueError(f'input.out.{what} is required')
    root = ctx.out_dir.resolve()
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f'input.out.{what} must be inside the job output folder')
    return path


async def scan_probe(inp, ctx):
    """Container header of an uploaded video: bytes, duration_s, width, height, fps, rotation_deg."""
    path = _scan_video(inp)
    try:
        from ..scan import video as scan_video
        info = await asyncio.to_thread(scan_video.probe, path)
    except ImportError as error:
        raise _scan_unavailable(error) from error
    return {'bytes': path.stat().st_size, **info}


async def scan_reconstruct(inp, ctx):
    """Keyframes, one shared camera, poses, floor and a mesh from a walk-around video."""
    from ..scan import ScanError
    path = _scan_video(inp)
    frames_dir = _scan_out_path(ctx, (inp.get('out') or {}).get('frames_dir'), 'frames_dir')
    mesh_path = _scan_out_path(ctx, (inp.get('out') or {}).get('mesh_path'), 'mesh_path')
    rotation = int(inp.get('rotation_deg') or 0)
    try:
        import av, cv2, open3d, scipy.optimize, torch, trimesh  # noqa: E401,F401  (availability check)
        from moge.model.v2 import MoGeModel  # noqa: F401
        from ..scan import dense, floor, sfm
        from ..scan import video as scan_video
    except (ImportError, OSError) as error:
        raise _scan_unavailable(error) from error
    import numpy as np
    progress = ctx.progress
    hint = 'Move slower, use more light and film textured surfaces.'
    timings = {}

    def work():
        t0 = time.monotonic()
        kept = scan_video.extract_keyframes(path, rotation, frames_dir, progress)
        frames = kept['frames']
        timings['frames_s'] = time.monotonic() - t0
        if len(frames) < 20:
            raise ScanError('scan_failed', f'Only {len(frames)} usable frames (at least 20 are needed). ' + hint)
        width, height = kept['width'], kept['height']
        files = [frames_dir / Path(f['image']).name for f in frames]

        def color(i):
            return cv2.imread(str(files[i]), cv2.IMREAD_COLOR)

        def gray(i):
            return cv2.cvtColor(color(i), cv2.COLOR_BGR2GRAY)

        rec = sfm.reconstruct(gray, color, len(frames), width, height, progress)
        timings.update(rec.timings)
        registered = sum(p is not None for p in rec.poses)
        if registered < 20 or registered < 0.5 * len(frames):
            raise ScanError('scan_failed', f'Only {registered} of {len(frames)} frames could be placed. ' + hint)
        t1 = time.monotonic()
        dense.undistort_frames(files, rec.poses, rec.camera, progress)       # frames become pure pinhole images
        timings['undistort_s'] = time.monotonic() - t1
        vertices, faces, colours, info = dense.fuse(rec, color, progress)
        timings.update(depth_s=info['depth_s'], fusion_s=info['fusion_s'])
        dense.write_glb(mesh_path, vertices, faces, colours, info['normals'])
        # floor: RANSAC over the sparse points and a sample of the fused vertices
        up, right = floor.camera_up(rec.poses)
        centres = np.array([p[:3, 3] for p in rec.poses if p is not None])
        sample = vertices[np.random.default_rng(0).choice(len(vertices), min(len(vertices), 40000), replace=False)]
        n, d = floor.find_floor(np.vstack([rec.points, sample]), up, centres)
        origin = centres.mean(axis=0)
        A = floor.alignment(n, d, x_hint=right, origin=origin)
        aligned = floor.apply(A, vertices)
        lo, hi = np.percentile(aligned, [0.5, 99.5], axis=0)
        progress('Floor: found')
        return kept, frames, rec, (n, d), A, vertices, faces, hi - lo

    kept, frames, rec, (n, d), A, vertices, faces, extent = await asyncio.to_thread(work)
    camera = {'model': 'SIMPLE_RADIAL', **rec.camera, 'undistorted': True}
    out_frames = [{'index': f['index'], 't_s': f['t_s'], 'image': f['image'],
                   'world_from_camera': None if p is None else np.asarray(p).tolist()}
                  for f, p in zip(frames, rec.poses)]
    return {'camera': camera, 'frames': out_frames,
            'floor': {'normal': [float(v) for v in n], 'd': float(d)}, 'alignment': A.tolist(),
            'mesh': {'file': mesh_path.name, 'vertices': int(len(vertices)), 'triangles': int(len(faces))},
            'stats': {'frames_total': len(frames), 'frames_registered': sum(p is not None for p in rec.poses),
                      'reproj_px': float(rec.reproj_px), 'points3d': int(len(rec.points)),
                      'extent': [float(v) for v in extent]},
            'timings_s': {k: round(v, 1) for k, v in timings.items()}}


async def scan_scale(inp, ctx):
    """ScanScale from a sheet of known size seen in a frame, or from a measured distance."""
    from ..scan import ScanError, scale
    from ..schemas import ScanScale
    method = inp.get('method')
    if method == 'distance':
        result = scale.scale_from_distance(inp.get('p1'), inp.get('p2'), inp.get('distance_m'))
    elif method == 'sheet':
        scan = inp.get('scan') or {}
        frames, camera, plane = scan.get('frames') or [], scan.get('camera'), scan.get('floor')
        index = inp.get('frame')
        if not camera or not plane:
            raise ScanError('scan_bad_scale', 'The scan has no camera and floor yet; reconstruct it first')
        frame = next((f for f in frames if f.get('index') == index), None)
        if frame is None or not frame.get('world_from_camera'):
            raise ScanError('scan_bad_scale', 'That frame is not part of the reconstruction; pick another frame')
        result = scale.scale_from_sheet(camera, frame['world_from_camera'], plane, inp.get('corners_px'), inp.get('size_m'),
                                        frame=int(index))
    else:
        raise ValueError(f'Unknown scale method {method!r}; use sheet or distance')
    return ScanScale.model_validate(result).model_dump(mode='json')


async def scan_floor(inp, ctx):
    """The floor re-picked from clicked points (>= 3, in R): a least-squares plane and its alignment."""
    import numpy as np
    from ..scan import floor
    n, d = floor.floor_from_points(inp.get('points'), inp.get('camera_up') or [0.0, 0.0, 1.0])
    A = floor.alignment(n, d, origin=np.asarray(inp['points'], dtype=float).mean(axis=0))
    return {'floor': {'normal': [float(v) for v in n], 'd': float(d)}, 'alignment': A.tolist()}


HANDLERS['scan_probe'] = scan_probe
HANDLERS['scan_reconstruct'] = scan_reconstruct
HANDLERS['scan_scale'] = scan_scale
HANDLERS['scan_floor'] = scan_floor
