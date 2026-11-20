"""A tool-call loop that works with any adapter, because the tools are described in the prompt and called
through plain JSON rather than through a provider's own tool API.

Each turn the model answers either
    {"tool": "<name>", "args": {...}}       to call a tool, or
    {"final": {...}}                        to finish.
Tool results are appended to the transcript and sent back on the next turn. Images a tool returns are passed
to the next call when the adapter can read images.
"""
import inspect
import json
from dataclasses import dataclass, field

from .base import LLMError, ask_json, supports_images


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON schema of args
    run: object       # callable(args) -> dict or (dict, list[Image]); may be async


@dataclass
class ToolResult:
    final: dict
    transcript: list = field(default_factory=list)


def describe(tools):
    return '\n'.join(f'- {t.name}: {t.description} Arguments (JSON schema): {json.dumps(t.parameters)}' for t in tools)


async def run_tools(adapter, system_prompt, task, tools, *, max_turns=6, validate_final=None, images=None):
    by_name = {t.name: t for t in tools}
    system = (system_prompt + '\n\nYou can call these tools before answering:\n' + describe(tools) +
              '\n\nEach reply is exactly one JSON object: {"tool": name, "args": {...}} to call one tool, or '
              '{"final": {...}} when you are done. Call a tool only when you need its result.')
    transcript, pending_images = [], list(images or [])
    for turn in range(max_turns):
        last = turn == max_turns - 1
        prompt = task + ''.join(f'\n\n[{i + 1}] {entry}' for i, entry in enumerate(transcript))
        if last:
            prompt += '\n\nNo more tool calls are allowed. Reply with {"final": {...}} now.'

        def check(data):
            if 'final' in data:
                if not isinstance(data['final'], dict):
                    raise ValueError('"final" must be an object')
                if validate_final:
                    validate_final(data['final'])
                return data
            if last:
                raise ValueError('Reply with {"final": {...}} now')
            if data.get('tool') not in by_name:
                raise ValueError(f'Unknown tool {data.get("tool")}; choose one of {sorted(by_name)} or reply with "final"')
            if not isinstance(data.get('args', {}), dict):
                raise ValueError('"args" must be an object')
            return data
        data = await ask_json(adapter, prompt, system, images=pending_images if supports_images(adapter) else None,
                              validate=check)
        pending_images = []
        if 'final' in data:
            return ToolResult(data['final'], transcript)
        tool = by_name[data['tool']]
        try:
            output = tool.run(data.get('args', {}))
            if inspect.isawaitable(output):
                output = await output
            if isinstance(output, tuple):
                output, pending_images = output
            result = json.dumps(output)[:12000]
        except Exception as error:  # a tool failure is reported to the model, not raised
            result = json.dumps({'error': str(error)[:500]})
        transcript.append(f'Tool call {data["tool"]}({json.dumps(data.get("args", {}))[:400]}) returned: {result}')
    raise LLMError('The model did not finish within the tool-call limit')
