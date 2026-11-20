"""`python -m studio.tasks <task>`: read the request from stdin, run the task, write protocol lines on stdout."""
import asyncio
import json
import os
import sys
import traceback
from pathlib import Path

from ..catalog.fetch import Downloaded
from . import handlers
from .llms import TaskLLMs
from .protocol import Emitter, TaskError

PROTOCOL = 1


def fixture_download():
    """Tests only (needs STUDIO_ALLOW_SCRIPTED=1). STUDIO_FAKE_DOWNLOAD=<file> serves that file for https://acme.example/
    URLs, so flows can run offline. When <file> ends in .json it is a map {url: path of a saved page} (relative paths
    are relative to the map file) and serves each listed URL (content type from the extension). Every other URL still
    goes through the real safe_download."""
    path = os.environ.get('STUDIO_FAKE_DOWNLOAD')
    if not path or os.environ.get('STUDIO_ALLOW_SCRIPTED') != '1':
        return None
    pages = None
    if path.endswith('.json'):
        base = Path(path).parent
        pages = {url: (base / file) for url, file in json.loads(Path(path).read_text()).items()}

    async def download(url, *args, **kwargs):
        if pages is not None and url in pages:
            file = pages[url]
            kind = {'.html': 'text/html', '.htm': 'text/html', '.pdf': 'application/pdf'}.get(file.suffix, 'text/plain')
            return Downloaded(bytes=file.read_bytes(), content_type=kind, final_url=url, filename=file.name)
        if pages is None and url.startswith('https://acme.example/'):
            return Downloaded(bytes=Path(path).read_bytes(), content_type='text/markdown', final_url=url, filename='ra6.md')
        return await handlers.safe_download(url, *args, **kwargs)
    return download


async def run(task, request, emitter):
    if request.get('protocol') != PROTOCOL:
        raise TaskError('value', f'Unsupported protocol version {request.get("protocol")!r}; this is protocol {PROTOCOL}')
    if request.get('task', task) != task:
        raise TaskError('value', f'Request is for task {request.get("task")!r}, not {task!r}')
    if task not in handlers.HANDLERS:
        raise TaskError('value', f'Unknown task {task!r}; tasks: {", ".join(handlers.HANDLERS)}')
    out_dir = Path(request.get('out_dir') or '')
    if not str(request.get('out_dir') or ''):
        raise TaskError('value', 'out_dir is required')
    out_dir.mkdir(parents=True, exist_ok=True)
    llm = request.get('llm') or {}
    context = request.get('context') or {}
    llms = TaskLLMs(llm.get('roles') or {}, on_usage=emitter.usage, out_dir=out_dir)
    emitter.secrets = llms.secrets() + [p.get('api_key') for p in [llm.get('profile') or {}] if p.get('api_key')]
    ctx = handlers.Context(llms=llms, out_dir=out_dir, progress=emitter.progress, profile=llm.get('profile'), context=context,
                           download=fixture_download() or handlers.safe_download)
    return await handlers.HANDLERS[task](request.get('input') or {}, ctx), out_dir


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    emitter = Emitter.install()
    emitter.start_heartbeat()
    try:
        if len(argv) != 1:
            raise TaskError('value', 'Usage: python -m studio.tasks <task>')
        try:
            request = json.loads(sys.stdin.read())
        except ValueError as error:
            raise TaskError('value', f'The request on stdin is not valid JSON: {error}') from error
        if not isinstance(request, dict):
            raise TaskError('value', 'The request on stdin must be a JSON object')
        data, out_dir = asyncio.run(run(argv[0], request, emitter))
        emitter.result(data, out_dir)
        return 0
    except BaseException as error:  # noqa: BLE001 - every failure becomes one error line
        if not isinstance(error, (TaskError, ValueError, FileNotFoundError)):
            traceback.print_exc()
        emitter.error(error)
        return 1
    finally:
        emitter.stop_heartbeat()
