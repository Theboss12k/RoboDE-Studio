"""Objects agent (agents/objects.py) and its place in design() / revise(): scripted LLMs, so these are fixture tests of
the validator and the pipeline logic, not model evaluations."""
import asyncio

import pytest
from fixtures_specs import bottle_spec
from studio_scripts import llms, scene_dict

from studio.agents import objects as agent
from studio.llm.registry import LLMRegistry
from studio.llm.scripted import ScriptedLLM
from studio.pipeline import design as pl
from studio.schemas import ObjectModel
from studio.tasks.handlers import version_json

SIZE = (0.07, 0.07, 0.22)


def part(shape, z, d, h, color='#336699', **kw):
    return {'shape': shape, 'center': [0, 0, z], 'size': [d, d, h], 'color': color, **kw}


def bottle(**changes):
    """A 0.07 x 0.07 x 0.22 m bottle: body, shoulder cone, neck, cap."""
    m = {'object_id': 'bottle', 'parts': [
        part('cylinder', -0.04, 0.07, 0.14, label='body'),
        part('cone', 0.06, 0.07, 0.04, label='shoulder'),
        part('cylinder', 0.085, 0.03, 0.05, label='neck'),
        part('cylinder', 0.1, 0.034, 0.02, '#ffffff', label='cap')]}
    m.update(changes)
    return m


def objects():
    return bottle_spec().objects


def check(*models):
    return agent.validate_models([ObjectModel.model_validate(m) for m in models], objects())


def test_validator_accepts_a_good_bottle():
    assert set(check(bottle())) == {'bottle'}


def test_validator_rejects_a_part_outside_the_box():
    m = bottle()
    m['parts'][3] = part('cylinder', 0.14, 0.034, 0.02, '#ffffff', label='cap')
    with pytest.raises(ValueError, match='outside the object box'):
        check(m)


def test_rotation_is_part_of_the_extent():
    m = bottle()
    m['parts'].append({'shape': 'box', 'center': [0, 0, 0], 'size': [0.01, 0.01, 0.2], 'rotation': [1.5708, 0, 0], 'color': '#111111'})
    with pytest.raises(ValueError, match='outside the object box'):      # lying down, it is 0.2 m long in y
        check(m)


def test_validator_rejects_too_small_coverage():
    m = bottle(parts=[part('cylinder', 0, 0.07, 0.1)])
    with pytest.raises(ValueError, match='cover only'):
        check(m)


def test_validator_rejects_a_missing_object():
    with pytest.raises(ValueError, match='has no model'):
        agent.validate_models([], objects())
    with pytest.raises(ValueError, match='unknown object'):
        check(bottle(object_id='ghost'))


def test_schema_rejects_bad_colour_and_too_many_parts():
    with pytest.raises(ValueError):
        ObjectModel.model_validate(bottle(parts=[part('box', 0, 0.07, 0.22, color='red')]))
    with pytest.raises(ValueError):
        ObjectModel.model_validate(bottle(parts=[part('box', 0, 0.07, 0.22)] * 25))


def registry(tmp_path, rule=None):
    over = llms()
    objs = ScriptedLLM([('simple 3D models', rule)] if rule is not None else [])
    over['objects'] = objs
    return LLMRegistry(tmp_path, over), objs


def run(coro):
    return asyncio.run(coro)


def test_design_attaches_models_and_reports(tmp_path):
    r, objs = registry(tmp_path, {'models': [bottle()]})
    lines = []
    v = run(pl.design(r, bottle_spec(), lines.append))
    o = v.scene.objects[0]
    assert o.model is not None and len(o.model.parts) == 4 and o.name == 'Bottle'
    assert lines.count('Object models: 1 made') == 1 and len(objs.calls) == 1
    out = version_json(v)['scene']['objects'][0]
    assert out['model']['parts'][0]['shape'] == 'cylinder' and out['name'] == 'Bottle'


def test_design_without_a_rule_is_as_before(tmp_path):
    r, objs = registry(tmp_path)
    lines = []
    v = run(pl.design(r, bottle_spec(), lines.append))
    assert v.scene.objects[0].model is None
    assert not any(line.startswith('Object models') for line in lines)
    out = version_json(v)['scene']['objects'][0]
    assert 'model' not in out and 'name' not in out


def test_design_survives_an_invalid_answer(tmp_path):
    r, objs = registry(tmp_path, {'models': [bottle(parts=[part('cylinder', 0, 0.07, 0.05)])]})
    lines = []
    v = run(pl.design(r, bottle_spec(), lines.append))
    assert v.scene.objects[0].model is None and len(objs.calls) == 2     # the answer and one repair round
    assert not any(line.startswith('Object models') for line in lines)


def test_revise_reuses_unchanged_models(tmp_path):
    r, objs = registry(tmp_path, {'models': [bottle()]})
    v1 = run(pl.design(r, bottle_spec(), lambda t: None)).model_copy(update={'version': 1})
    assert len(objs.calls) == 1
    r.overrides['revise'] = ScriptedLLM([('change an existing', {'targets': ['scene'], 'summary': 'A crate',
        'scene': {**scene_dict(), 'boxes': [*scene_dict()['boxes'], {'id': 'crate', 'kind': 'box', 'center': [0.5, 2.0, 0.2], 'size': [0.2, 0.2, 0.4]}]}})])
    lines = []
    v2 = run(pl.revise(r, v1, 'add a crate', None, lines.append, auto_repair=False))
    assert len(objs.calls) == 1                                             # no second call
    assert v2.scene.objects[0].model == v1.scene.objects[0].model and v2.scene.objects[0].name == 'Bottle'
    assert not any(line.startswith('Object models') for line in lines)


def test_revise_asks_again_for_a_changed_object(tmp_path):
    r, objs = registry(tmp_path, {'models': [bottle()]})
    v1 = run(pl.design(r, bottle_spec(), lambda t: None)).model_copy(update={'version': 1})
    spec = v1.spec.model_dump(mode='json')
    spec['objects'][0]['name'] = 'Water bottle'
    r.overrides['revise'] = ScriptedLLM([('change an existing', {'targets': ['spec'], 'spec': spec, 'summary': 'Renamed'})])
    lines = []
    v2 = run(pl.revise(r, v1, 'call it a water bottle', None, lines.append, auto_repair=False))
    assert len(objs.calls) == 2 and v2.scene.objects[0].name == 'Water bottle'
    assert 'Object models: 1 made' in lines
