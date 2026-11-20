#!/usr/bin/env python3
"""Regenerates rules.json (scripted LLM answers for the Java integration tests) from the pytest fixtures.

Run from the repo root:  .venv/bin/python server-java/src/test/resources/scripted/make_fixtures.py
The answers are fixtures (bottle task A to B, RA-6 arm datasheet), not model output.
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'tests')]

from fixtures_specs import bottle_spec  # noqa: E402
from studio.catalog.fetch import Downloaded  # noqa: E402
from studio.llm.scripted import ScriptedLLM  # noqa: E402
from studio.tasks import handlers  # noqa: E402
from studio.tasks.llms import TaskLLMs  # noqa: E402
from studio_scripts import ROBOT, scene_dict, spec_dict  # noqa: E402
from test_api import DATASHEET, analysis_reply  # noqa: E402
from test_catalog import answer as search_answer, values_answer  # noqa: E402

QUESTIONS = {'questions': [{'id': 'cycle', 'text': 'Cycle time?', 'kind': 'number', 'unit': 's', 'default': 6}]}
moved = bottle_spec()
moved.markers[1].position = (0.5, -0.5, 0.75)
REVISE = {'targets': ['spec', 'scene'], 'spec': moved.model_dump(mode='json'), 'scene': scene_dict(moved),
          'summary': 'B 20 cm further'}


async def revised(base):
    """v2 as the revise task produces it from v1 with the REVISE patch (what the golden flow approves)."""
    import tempfile
    kw = {'revise': ScriptedLLM([('change an existing', REVISE)])}

    class Fixed(TaskLLMs):
        def for_role(self, role):
            return kw[role]
    ctx = handlers.Context(Fixed({}), Path(tempfile.mkdtemp()), lambda t: None)
    out = await handlers.revise({'base': base, 'request': 'make it reach 20 cm further', 't': None}, ctx)
    return {**out['version'], 'version': 2}


async def analysis_items(base):
    """The differences the analysis agent is asked to explain for the RA-6 candidate (captured, then answered)."""
    seen = {}

    def spy(input, system, images):
        seen['items'] = json.loads(input.split('Differences (JSON):\n', 1)[1])
        return analysis_reply(input, system, images)
    kw = {'extract': ScriptedLLM([("manufacturer's datasheet", values_answer())]),
          'analysis': ScriptedLLM([('explain, in plain', spy)])}

    class Fixed(TaskLLMs):
        def for_role(self, role):
            return kw[role]

    async def download(url):
        return Downloaded(bytes=DATASHEET.encode(), content_type='text/markdown', final_url=url, filename='ra6.md')
    import tempfile
    out = Path(tempfile.mkdtemp())
    ctx = handlers.Context(Fixed({}), out, lambda t: None, download=download)
    version = base
    cand = search_answer()['candidates'][0]
    cand = {'id': 'acme_ra_6', 'manufacturer': cand['manufacturer'], 'model': cand['model'], 'family': cand['family'],
            'product_url': cand.get('product_url', ''), 'datasheet_url': 'https://acme.example/ra6.md'}
    await handlers.analyse({'version': version, 'candidate': cand, 'datasheet_url': 'https://acme.example/ra6.md',
                            'document_path': None}, ctx)
    return seen['items']


from test_tasks import _bottle_version  # noqa: E402

from test_tasks import _bottle_version  # noqa: E402


def rules_for(items):
    return [
        {'match': 'connection test', 'answers': ['ready']},
        {'match': '3D scene', 'answers': [scene_dict()]},
        {'match': 'kind of robot', 'answers': [ROBOT]},
        {'match': 'change an existing', 'answers': [REVISE]},
        {'match': 'find real robots', 'answers': [search_answer()]},
        {'match': "manufacturer's datasheet", 'answers': [values_answer()]},
        {'match': 'explain, in plain', 'answers': [{'differences': [{**d, 'impact': f'{d["topic"]}: fine for this task.'} for d in items]}]},
        {'match': '"cycle":', 'answers': [{'spec': spec_dict()}]},          # round 2: the answers are in the input
        {'match': 'requirements engineer', 'answers': [QUESTIONS]},          # round 1: ask
    ]


v1 = _bottle_version()
v2 = asyncio.run(revised(v1))
# rules.json: the analysis answer fits an approved v1; rules_v2.json: an approved v2 (the golden flow revises, then approves)
for name, version in (('rules.json', {**v1, 'version': 1}), ('rules_v2.json', v2)):
    items = asyncio.run(analysis_items(version))
    (Path(__file__).parent / name).write_text(json.dumps({'rules': rules_for(items)}, indent=1))
    print('wrote', name, len(items), 'differences')
