"""The provider-independent LLM contract.

Every provider is an adapter with

    async def invoke(input: str, system_prompt: str) -> str

and, when it can see images,

    async def invoke_multimodal(input: str, system_prompt: str, images: list[Image]) -> str

Adapters know nothing about robots, simulators or targets. Callers that need JSON use `ask_json`, which
works with any adapter: it asks for JSON in the prompt, parses the answer, and retries once with the parse
error. An adapter may also offer `structured(schema)`, returning a copy that enforces a JSON schema natively.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class LLMError(RuntimeError):
    """The model could not be reached or did not give a usable answer."""


@dataclass
class Image:
    data_base64: str
    mime_type: str = 'image/jpeg'
    label: str = ''


@runtime_checkable
class LLMAdapter(Protocol):
    async def invoke(self, input: str, system_prompt: str) -> str: ...


@dataclass
class Call:
    """A record of one model call, kept for the session log (never the prompts' private content on disk)."""
    adapter: str
    model: str
    seconds: float
    images: int = 0
    extra: dict = field(default_factory=dict)


def supports_images(adapter):
    return callable(getattr(adapter, 'invoke_multimodal', None))


def json_answer(text):
    """Parse a JSON object from a model answer: raw JSON, one fenced block, or the first {...} span."""
    text = re.sub(r'^\s*<think>.*?</think>', '', text or '', flags=re.S).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    fenced = re.findall(r'```(?:json)?\s*(.*?)```', text, flags=re.S)
    if len(fenced) == 1:
        return json.loads(fenced[0])
    start, end = text.find('{'), text.rfind('}')
    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError('The answer contained no JSON object')


def portable_schema(schema):
    """A copy of a JSON schema that strict validators accept: fixed-length tuples (`prefixItems`, which pydantic emits
    for tuple fields and the Claude CLI's strict --json-schema rejects) become arrays with minItems/maxItems."""
    if isinstance(schema, list):
        return [portable_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {key: portable_schema(value) for key, value in schema.items() if key != 'prefixItems'}
    if 'prefixItems' in schema:
        items = [portable_schema(item) for item in schema['prefixItems']]
        out['items'] = items[0] if all(item == items[0] for item in items) else {}
        out.setdefault('minItems', len(items))
        out.setdefault('maxItems', len(items))
    return out


def schema_instruction(schema):
    return ('\n\nAnswer with one JSON object that matches this JSON schema, and nothing else:\n'
            + json.dumps(portable_schema(schema), separators=(',', ':')))


async def ask_json(adapter, input, system_prompt, *, schema=None, images=None, validate=None, native=True):
    """Ask for a JSON object and return it parsed (and validated, when `validate` is given).

    With `native` and an adapter that offers `structured(schema)`, the provider enforces the schema (the Claude CLI's
    --json-schema: one extra turn, but long answers come out more compact). Otherwise the schema goes into the system
    prompt, where it is cached with the rest of it; the operator, with many short answers, chooses this. The schema
    is never sent twice.
    `validate(data)` may raise ValueError; the message is sent back once so the model can repair its answer.
    """
    schema = portable_schema(schema) if schema is not None else None
    enforced = native and schema is not None and hasattr(adapter, 'structured')
    model = adapter.structured(schema) if enforced else adapter
    system = system_prompt
    if schema is not None and not enforced:
        system += schema_instruction(schema)
    prompt, last = input, None
    for attempt in range(2):
        if images:
            if not supports_images(model):
                raise LLMError('This LLM profile cannot read images; choose one with image support')
            answer = await model.invoke_multimodal(prompt, system, images)
        else:
            answer = await model.invoke(prompt, system)
        try:
            data = json_answer(answer)
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object')
            return validate(data) if validate else data
        except (ValueError, TypeError, KeyError) as error:
            last = error
            prompt = (f'{input}\n\nYour previous answer could not be used: {str(error)[:600]}\n'
                      'Answer again with one corrected JSON object only.')
    raise LLMError(f'The model did not return usable JSON: {last}')
