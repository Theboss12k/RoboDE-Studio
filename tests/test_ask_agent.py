"""The Ask agent with a scripted (fixture) LLM: proposals are only recorded, citations are checked against the grounding.
These are fixture tests, not model evaluations."""
import asyncio
import json

from studio.agents import ask
from studio.llm.scripted import ScriptedLLM

GROUNDING = {
    'project': {'id': 'p_1', 'title': 'Bottle', 'brief': 'Move a bottle', 'stage': 'design',
                'versions': [{'version': 1, 'parent_version': None, 'summary': 'first'}, {'version': 2, 'parent_version': 1, 'summary': 'longer'}]},
    'version': {'version': 2, 'spec': {'title': 'Bottle'}, 'requirements': [{'id': 'reach_max', 'target': 0.6, 'unit': 'm'}],
                'checks': [{'requirement_id': 'reach_max', 'actual': 0.7, 'required': 0.6, 'unit': 'm', 'passed': True}]},
    'build': {'build': 3, 'motors': [{'joint': 'j1', 'checks': []}], 'electronics': [{'id': 'drv1'}]},
}


def sequence(*answers):
    """A scripted LLM that answers the given replies in order (the last one repeats)."""
    queue = list(answers)

    def reply(_input, _system, _images):
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        return item if isinstance(item, str) else json.dumps(item)
    return ScriptedLLM([('Ask bar', reply)])


def call(kind, **args):
    return {'tool': kind, 'args': args}


def final(**fields):
    return {'final': {'answer': 'ok', 'citations': [], 'confirm': [], 'navigate': None, **fields}}


def run(llm, question='Is the reach enough?', grounding=GROUNDING):
    return asyncio.run(ask.run(llm, question, [], {'project_id': 'p_1'}, grounding))


def test_records_two_actions_and_rejects_a_third():
    llm = sequence(call('revise', request='make it 20 cm longer', base_version=2), call('search'),
                   call('update_brief', text='A new brief of the job'), final(answer='Queued two actions.'))
    out = run(llm)
    assert [p['kind'] for p in out['proposals']] == ['revise', 'search']
    assert out['proposals'][0]['args'] == {'request': 'make it 20 cm longer', 'base_version': 2}
    assert out['answer'] == 'Queued two actions.'
    # the third call got the limit message back and nothing was recorded for it
    assert 'limit: 2 actions per question' in llm.calls[-1]['input']
    assert '"queued": true' in llm.calls[1]['input']


def test_a_bad_argument_is_reported_to_the_model_and_not_recorded():
    llm = sequence(call('revise', request=''), call('revise', request='shorter arm'), final())
    out = run(llm)
    assert out['proposals'] == [{'kind': 'revise', 'args': {'request': 'shorter arm'}}]
    assert 'request must be a string' in llm.calls[1]['input']


def test_unknown_citations_are_dropped():
    cites = [{'kind': 'check', 'ref': 'reach_max', 'label': 'Reach'}, {'kind': 'check', 'ref': 'made_up', 'label': 'Nope'},
             {'kind': 'version', 'ref': '2', 'label': 'v2'}, {'kind': 'version', 'ref': '9', 'label': 'v9'},
             {'kind': 'motor', 'ref': 'drv1', 'label': 'driver'}, {'kind': 'build', 'ref': '3', 'label': 'b3'},
             {'kind': 'brief', 'ref': 'brief', 'label': 'Brief'}, {'kind': 'candidate', 'ref': 'x', 'label': 'x'},
             {'kind': 'weird', 'ref': 'x', 'label': 'x'}]
    out = run(sequence(final(citations=cites)))
    assert [(c['kind'], c['ref']) for c in out['citations']] == [('check', 'reach_max'), ('version', '2'), ('motor', 'drv1'),
                                                                 ('build', '3'), ('brief', 'brief')]


def test_confirm_chips_pass_through_and_navigate():
    confirm = [{'kind': 'approve', 'label': 'Approve v2', 'request': {'version': 2}},
               {'kind': 'decision', 'label': 'Build it myself', 'request': {'action': 'self', 'build': 3}}]
    out = run(sequence(final(confirm=confirm, navigate={'stage': 'studio', 'build': 3})))
    assert out['confirm'] == confirm
    assert out['navigate'] == {'stage': 'studio', 'build': 3}
    assert out['proposals'] == []


def test_a_malformed_confirm_is_sent_back_to_the_model():
    bad = {'kind': 'approve', 'label': 'Approve', 'request': {'version': 'two'}}
    good = {'kind': 'approve', 'label': 'Approve', 'request': {'version': 2}}
    llm = sequence(final(confirm=[bad]), final(confirm=[good]))
    out = run(llm)
    assert out['confirm'] == [good]
    assert 'request.version' in llm.calls[1]['input']


def test_approve_is_not_a_tool():
    llm = sequence(call('approve', version=2), final())
    out = run(llm)
    assert out['proposals'] == []
    assert 'Unknown tool approve' in llm.calls[1]['input']


def test_prompt_marks_stored_text_as_data():
    assert 'never instructions' in ask.SYSTEM and 'at most 2 actions' in ask.SYSTEM.lower().replace('{', '')


def test_approve_build_chip_needs_the_build_number():
    bad = {'kind': 'approve_build', 'label': 'Approve build', 'request': {}}
    good = {'kind': 'approve_build', 'label': 'Approve build', 'request': {'build': 3}}
    llm = sequence(final(confirm=[bad]), final(confirm=[good]))
    assert run(llm)['confirm'] == [good]
