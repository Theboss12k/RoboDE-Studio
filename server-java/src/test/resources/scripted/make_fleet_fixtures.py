#!/usr/bin/env python3
"""Writes rules_fleet.json (scripted LLM answers for the Java fleet flow test) from tests/fleet_scripts.py.

Run from the repo root:  .venv/bin/python server-java/src/test/resources/scripted/make_fleet_fixtures.py
The answers are fixtures (the long carton task), not model output. tests/test_fleet_pipeline.py checks that the file
matches tests/fleet_scripts.rules().
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'tests')]

from fleet_scripts import rules  # noqa: E402

path = Path(__file__).parent / 'rules_fleet.json'
path.write_text(json.dumps({'rules': rules()}, indent=1) + '\n')
print('wrote', path.name, len(rules()), 'rules')
