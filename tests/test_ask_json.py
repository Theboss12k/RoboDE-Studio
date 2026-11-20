"""ask_json feeds every rejected answer back to the model (with all earlier errors) before it gives up."""
import json

import pytest

from studio.llm.base import JSON_REPAIRS, LLMError, ask_json


class Seq:
    """An adapter that answers from a list and records the prompts it was given."""
    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    async def invoke(self, input, system_prompt):
        self.prompts.append(input)
        return self.answers.pop(0)


def needs_table(data):
    if data.get('table') is not True:
        raise ValueError('Marker "bottle_start" is at z = 0.75 with nothing under it: add a table')
    return data


async def test_rejected_answers_are_fed_back_until_one_passes():
    bad = json.dumps({'table': False})
    model = Seq([bad, 'not json', bad, json.dumps({'table': True})])
    data = await ask_json(model, 'make a scene', 'system', validate=needs_table)
    assert data == {'table': True} and len(model.prompts) == 4 == 1 + JSON_REPAIRS
    last = model.prompts[-1]
    assert last.startswith('make a scene') and 'attempt 1: Marker "bottle_start"' in last
    assert 'attempt 2:' in last and 'attempt 3: Marker' in last   # every earlier error is listed


async def test_gives_up_after_the_repairs():
    model = Seq([json.dumps({'table': False})] * (1 + JSON_REPAIRS))
    with pytest.raises(LLMError, match=f'after {1 + JSON_REPAIRS} attempts: Marker "bottle_start"'):
        await ask_json(model, 'make a scene', 'system', validate=needs_table)
    assert len(model.prompts) == 1 + JSON_REPAIRS


async def test_repairs_can_be_limited_per_call():
    model = Seq([json.dumps({'table': False})] * 2)
    with pytest.raises(LLMError, match='after 2 attempts'):
        await ask_json(model, 'make a scene', 'system', validate=needs_table, repairs=1)


def test_portable_schema_drops_vendor_keywords_for_strict_validators():
    """The Claude CLI's strict --json-schema rejects unknown keywords: the `x-optional` marker on TechnicalSpec.baseline
    made every real spec call fail (2026-11-21). Field names are kept, even unusual ones."""
    from studio.llm.base import portable_schema
    from studio.schemas import TechnicalSpec

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for v in node:
                yield from keys(v)

    raw = TechnicalSpec.model_json_schema()
    assert 'x-optional' in set(keys(raw))
    clean = portable_schema(raw)
    assert not any(k.startswith('x-') for k in keys(clean))
    assert 'baseline' in clean['properties']
    assert portable_schema({'properties': {'x-name': {'type': 'string', 'x-optional': True}}}) == \
        {'properties': {'x-name': {'type': 'string'}}}
