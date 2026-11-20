#!/usr/bin/env python3
"""Writes rules_studio.json (scripted LLM answers for the Java robot-studio flow test) from tests/.

Run from the repo root:  .venv/bin/python server-java/src/test/resources/scripted/make_studio_fixtures.py
The answers are fixtures, not model output: the bottle task (spec, scene, 6-axis arm), a motor search whose candidates
point at the saved shop pages in tests/fixtures/shops/ (served by the fake download map, see below) and a structure
style. tests/test_studio_pipeline.py checks that the file matches `rules()`.

The Java test must start the Python tasks with STUDIO_ALLOW_SCRIPTED=1 and STUDIO_FAKE_DOWNLOAD=<a .json map
{"<product url>": "<absolute path of the saved page>"}; `studio_build_scripts.fake_download_map(dir)` writes one and
the urls are ROBU and SOL there (https://robu.in/product/nema23-3nm, https://www.omc-stepperonline.com/nema23-1-26nm,
pages robu_nema23_3nm.html and stepperonline_23hs_1_26nm.html).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'tests')]

from studio_build_scripts import SEARCH_MATCH, STYLE_MATCH, motor_answer, style_answer  # noqa: E402
from studio_scripts import ROBOT, scene_dict, spec_dict  # noqa: E402


def rules():
    return [{'match': 'connection test', 'answers': ['ready']},
            {'match': '3D scene', 'answers': [scene_dict()]},
            {'match': 'kind of robot', 'answers': [ROBOT]},
            {'match': SEARCH_MATCH, 'answers': [motor_answer()]},
            {'match': STYLE_MATCH, 'answers': [style_answer()]},
            {'match': 'requirements engineer', 'answers': [{'spec': spec_dict()}]}]


if __name__ == '__main__':
    path = Path(__file__).parent / 'rules_studio.json'
    path.write_text(json.dumps({'rules': rules()}, indent=1) + '\n')
    print('wrote', path.name, len(rules()), 'rules')
