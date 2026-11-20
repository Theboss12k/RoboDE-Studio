import os

import pytest

from studio.agents import spec
from studio.llm.claude_cli import ClaudeCLI


@pytest.mark.claude
async def test_spec_agent_with_real_claude_for_the_bottle_brief():
    """Real model inference (needs STUDIO_CLAUDE=1 and the Claude Code CLI signed in)."""
    adapter = ClaudeCLI(model=os.environ.get('STUDIO_CLAUDE_MODEL', 'claude-sonnet-5-5'))
    brief = 'Move a 0.3 kg bottle, 70 mm wide and 22 cm tall, from point A to point B on a table, 6 seconds per cycle.'
    result = await spec.run(adapter, brief, None, {}, 3)   # last round: a spec with assumptions is required
    assert 'spec' in result and result['spec'].physics_inputs.object_mass_kg == pytest.approx(0.3, rel=0.2)
