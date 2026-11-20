"""File store of projects.

runtime/projects/<id>/
    project.json            brief, stage, questions, answers, round, spec, selected_candidate, decision, timestamps
    versions/v<n>.json      DesignVersion; written once, only the `approved` flag changes later
    reference/              text.md, robot.urdf + urdf.json (summary), images/<name>
    candidates.json         list[Candidate]
    documents/<cid>.txt     datasheet text (downloaded or uploaded)
    analysis/<cid>.json     Analysis
"""
import re
import secrets
import shutil
import threading
from pathlib import Path

from ..ingest import images as image_intake
from ..ingest import urdf as urdf_intake
from ..schemas import Analysis, Candidate, DesignVersion
from ..util import atomic_json, now, read_json, safe_id, safe_name

PROJECT_ID = re.compile(r'^p_[0-9a-f]{10}$')
MAX_TEXT = 60_000


def project_id(value):
    if not isinstance(value, str) or not PROJECT_ID.match(value):
        raise FileNotFoundError('Project not found')
    return value


class ProjectStore:
    def __init__(self, runtime):
        self.root = Path(runtime) / 'projects'
        self.lock = threading.Lock()

    def create(self, brief):
        brief = (brief or '').strip()
        if not brief:
            raise ValueError('Describe the job first')
        pid = 'p_' + secrets.token_hex(5)
        meta = {'id': pid, 'title': brief.split('\n')[0][:80], 'brief': brief[:8000], 'stage': 'brief', 'questions': [],
                'answers': {}, 'round': 0, 'spec': None, 'approved_version': None, 'selected_candidate': None,
                'decision': None, 'created': now(), 'updated': now()}
        atomic_json(self.root / pid / 'project.json', meta)
        return self.open(pid)

    def open(self, pid):
        return Project(self, project_id(pid))

    def list(self):
        out = []
        for path in sorted(self.root.glob('p_*/project.json')) if self.root.exists() else []:
            meta = read_json(path) or {}
            out.append({k: meta.get(k) for k in ('id', 'title', 'stage', 'created', 'updated')})
        return sorted(out, key=lambda m: m['updated'] or '', reverse=True)

    def delete(self, pid):
        project = self.open(pid)
        shutil.rmtree(project.dir)


class Project:
    def __init__(self, store, pid):
        self.store, self.id = store, pid
        self.dir = store.root / pid
        if not (self.dir / 'project.json').exists():
            raise FileNotFoundError('Project not found')

    # -- metadata -------------------------------------------------------------------------------------------------
    @property
    def meta(self):
        return read_json(self.dir / 'project.json')

    def update(self, **fields):
        with self.store.lock:
            meta = {**self.meta, **fields, 'updated': now()}
            atomic_json(self.dir / 'project.json', meta)
        return meta

    @property
    def brief(self):
        return self.meta['brief']

    @property
    def spec(self):
        from ..schemas import TechnicalSpec
        data = self.meta.get('spec')
        return TechnicalSpec.model_validate(data) if data else None

    # -- reference ------------------------------------------------------------------------------------------------
    @property
    def ref(self):
        return self.dir / 'reference'

    def reference_view(self):
        text = (self.ref / 'text.md').read_text() if (self.ref / 'text.md').exists() else ''
        files = []
        if (self.ref / 'robot.urdf').exists():
            files.append({'name': read_json(self.ref / 'urdf.json', {}).get('filename', 'robot.urdf'), 'kind': 'urdf'})
        files += [{'name': p.name, 'kind': 'image'} for p in sorted((self.ref / 'images').glob('*'))] if (self.ref / 'images').exists() else []
        return {'text': text, 'files': files} if text or files else None

    def set_reference_text(self, text):
        text = (text or '').strip()
        if len(text) > MAX_TEXT:
            raise ValueError(f'Reference text must be at most {MAX_TEXT} characters')
        self.ref.mkdir(parents=True, exist_ok=True)
        if text:
            (self.ref / 'text.md').write_text(text)
        else:
            (self.ref / 'text.md').unlink(missing_ok=True)

    def add_reference_file(self, filename, payload):
        name = safe_name(filename)
        self.ref.mkdir(parents=True, exist_ok=True)
        if name.lower().endswith(('.urdf', '.zip')):
            _, path, files = urdf_intake.archive_files(payload, name)
            data = urdf_intake.parse_urdf(files[path])
            (self.ref / 'robot.urdf').write_bytes(files[path])
            atomic_json(self.ref / 'urdf.json', {'filename': name, 'summary': urdf_intake.summary(data)})
            return {'name': name, 'kind': 'urdf'}
        image_intake.check_image(payload)
        (self.ref / 'images').mkdir(exist_ok=True)
        if len(list((self.ref / 'images').glob('*'))) >= 6:
            raise ValueError('At most 6 reference images')
        (self.ref / 'images' / name).write_bytes(payload)
        return {'name': name, 'kind': 'image'}

    def clear_reference(self):
        shutil.rmtree(self.ref, ignore_errors=True)

    def reference_for_agent(self):
        """{'text', 'urdf', 'images': [Image]} or None."""
        from ..llm.base import Image
        import base64
        view = self.reference_view()
        if not view:
            return None
        images = []
        for p in sorted((self.ref / 'images').glob('*')) if (self.ref / 'images').exists() else []:
            payload = p.read_bytes()
            images.append(Image(base64.b64encode(payload).decode(), image_intake.image_type(payload) or 'image/jpeg', p.name))
        return {'text': view['text'], 'urdf': (read_json(self.ref / 'urdf.json') or {}).get('summary'), 'images': images}

    # -- versions -------------------------------------------------------------------------------------------------
    def versions(self):
        folder = self.dir / 'versions'
        return sorted(int(p.stem[1:]) for p in folder.glob('v*.json')) if folder.exists() else []

    def version(self, n):
        data = read_json(self.dir / 'versions' / f'v{int(n)}.json')
        if data is None:
            raise FileNotFoundError(f'Version {n} not found')
        return DesignVersion.model_validate(data)

    def latest(self):
        versions = self.versions()
        return self.version(versions[-1]) if versions else None

    def add_version(self, build):
        """`build(n)` returns the DesignVersion with number n. Versions are never overwritten."""
        with self.store.lock:
            n = (self.versions() or [0])[-1] + 1
            version = build(n)
            path = self.dir / 'versions' / f'v{n}.json'
            if path.exists():
                raise ValueError('Version already exists')
            atomic_json(path, version.model_dump(mode='json'))
        self.update(stage='designed')
        return version

    def approve(self, n):
        version = self.version(n)
        with self.store.lock:
            for m in self.versions():
                data = read_json(self.dir / 'versions' / f'v{m}.json')
                if data['approved'] != (m == n):
                    data['approved'] = m == n  # the only field that changes after creation
                    atomic_json(self.dir / 'versions' / f'v{m}.json', data)
        self.update(approved_version=n, stage='approved')
        return version

    def approved(self):
        n = self.meta.get('approved_version')
        return self.version(n) if n else None

    # -- candidates, documents, analyses ---------------------------------------------------------------------------
    def candidates(self):
        return [Candidate.model_validate(c) for c in read_json(self.dir / 'candidates.json', [])]

    def save_candidates(self, candidates):
        atomic_json(self.dir / 'candidates.json', [c.model_dump(mode='json') for c in candidates])
        self.update(stage='candidates')

    def candidate(self, cid):
        for c in self.candidates():
            if c.id == safe_id(cid):
                return c
        raise FileNotFoundError('Candidate not found')

    def save_document(self, cid, text, source=''):
        path = self.dir / 'documents' / f'{safe_id(cid)}.txt'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        atomic_json(path.with_suffix('.json'), {'source': source})

    def document_source(self, cid):
        return (read_json(self.dir / 'documents' / f'{safe_id(cid)}.json') or {}).get('source', '')

    def document(self, cid):
        path = self.dir / 'documents' / f'{safe_id(cid)}.txt'
        return path.read_text() if path.exists() else None

    def save_analysis(self, analysis: Analysis):
        atomic_json(self.dir / 'analysis' / f'{safe_id(analysis.candidate_id)}.json', analysis.model_dump(mode='json'))
        self.update(selected_candidate=analysis.candidate_id, stage='analysed')

    def analysis(self, cid):
        data = read_json(self.dir / 'analysis' / f'{safe_id(cid)}.json')
        if data is None:
            raise FileNotFoundError('No analysis for this candidate yet')
        return Analysis.model_validate(data)

    def analyses(self):
        folder = self.dir / 'analysis'
        return sorted(p.stem for p in folder.glob('*.json')) if folder.exists() else []

    def set_decision(self, candidate_id, action):
        if candidate_id not in self.analyses():
            raise ValueError('Analyse this candidate before deciding')
        self.update(decision={'candidate_id': candidate_id, 'action': action}, stage='decided')

    # -- the object the API returns ---------------------------------------------------------------------------------
    def view(self):
        m = self.meta
        return {'id': self.id, 'title': m['title'], 'brief': m['brief'], 'reference': self.reference_view(), 'stage': m['stage'],
                'questions': m['questions'], 'answers': m['answers'], 'versions': self.versions(),
                'approved_version': m['approved_version'], 'candidates_count': len(self.candidates()),
                'selected_candidate': m['selected_candidate'], 'analyses': self.analyses(), 'decision': m['decision'],
                'created': m['created'], 'updated': m['updated']}
