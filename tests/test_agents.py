from io import BytesIO
import json
from unittest.mock import Mock

from pydantic import BaseModel
import pytest
from paperlab.agents import BedrockAgents, CitationRepairs
from paperlab.config import Settings


class Output(BaseModel):
    answer: str


@pytest.mark.parametrize('worker,effort,budget', [(False, 'high', 32768), (True, 'low', 8192)])
def test_kimi_uses_native_reasoning_controls_and_never_records_reasoning(store, worker, effort, budget):
    agent = BedrockAgents.__new__(BedrockAgents)
    agent.settings, agent.store, agent.project, agent.run, agent.calls = Settings(), store, 'p', 'r', 0
    events = []
    agent.notify = lambda kind, summary, detail: events.append((kind, detail))
    agent.client = Mock()
    agent.client.invoke_model.return_value = {'body': BytesIO(json.dumps({
        'choices': [{'finish_reason': 'stop', 'message': {'content': '{"answer":"ok"}', 'reasoning_content': 'DO_NOT_RECORD_REASONING'}}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30},
    }).encode())}
    output = agent.call('test', {'input': 'evidence'}, Output, worker=worker)
    assert output.answer == 'ok'
    request = json.loads(agent.client.invoke_model.call_args.kwargs['body'])
    assert request['reasoning_effort'] == effort and request['max_tokens'] == budget
    assert agent.client.invoke_model.call_args.kwargs['modelId'] == 'us.moonshotai.kimi-k3'
    agent.client.converse.assert_not_called()
    record_event = next(detail for kind, detail in events if kind == 'agent_completed')
    body = store.blob_get(record_event['artifact'])
    assert b'DO_NOT_RECORD_REASONING' not in body
    assert json.loads(body)['usage']['totalTokens'] == 30


def test_incomplete_kimi_reply_records_safe_stop_reason_and_rejects_partial_json(store):
    agent = BedrockAgents.__new__(BedrockAgents)
    agent.settings, agent.store, agent.project, agent.run, agent.calls = Settings(), store, 'p', 'r', 0
    events = []
    agent.notify = lambda kind, summary, detail: events.append((kind, detail))
    agent.client = Mock()
    agent.client.invoke_model.return_value = {'body': BytesIO(json.dumps({
        'choices': [{'finish_reason': 'length', 'message': {'content': '{"answer":'}}],
        'usage': {'completion_tokens': 32768},
    }).encode())}
    with pytest.raises(ValueError, match='incomplete'):
        agent.call('test', {}, Output)
    response = next(detail for kind, detail in events if kind == 'agent_response')
    assert response['stop_reason'] == 'length'
    assert not any(kind == 'agent_completed' for kind, _ in events)


def citation_plan(experiment):
    from paperlab.models import PaperPlan
    return PaperPlan(title='Paper', main_claim='Momentum', supporting_claims=[],
                     citations=[{'page': 1, 'quote': 'winners'}], experiments=[experiment])


def test_planner_repairs_wrong_pdf_pages_with_exact_match_feedback(experiment):
    bad = citation_plan(experiment)
    fixed = bad.model_copy(deep=True)
    fixed.citations[0].page = 2
    fixed.experiments[0].citations[0].page = 2
    agent = BedrockAgents.__new__(BedrockAgents)
    repairs = CitationRepairs(repairs=[{"path": "citations[0]", "citation": fixed.citations[0]},
                                       {"path": "experiments[0].citations[0]", "citation": fixed.experiments[0].citations[0]}])
    agent.call = Mock(side_effect=[bad, repairs])
    agent.notify = Mock()
    assert agent.plan([{'page': 1, 'text': 'other'}, {'page': 2, 'text': 'winners'}]) == fixed
    feedback = agent.call.call_args.args[1]['citation_errors']
    assert len(feedback) == 2
    assert all(issue['matching_pdf_pages'] == [2] for issue in feedback)
    assert feedback[1]['path'] == 'experiments[0].citations[0]'


def test_planner_citation_repair_budget_does_not_accept_invented_quotes(experiment):
    from paperlab.agents import CitationValidationError
    bad = citation_plan(experiment)
    agent = BedrockAgents.__new__(BedrockAgents)
    repairs = CitationRepairs(repairs=[{"path": "citations[0]", "citation": bad.citations[0]}])
    agent.call = Mock(side_effect=[bad, repairs, repairs])
    agent.notify = Mock()
    with pytest.raises(CitationValidationError):
        agent.plan([{'page': 1, 'text': 'unrelated'}])
    assert agent.call.call_count == 3
    assert agent.notify.call_args.args[2]['repair_available'] is False


def test_citation_repair_cannot_drop_experiments_or_change_claim(experiment):
    bad = citation_plan(experiment)
    changed = CitationRepairs(repairs=[{"path": "main_claim", "citation": bad.citations[0]}])
    agent = BedrockAgents.__new__(BedrockAgents)
    agent.call = Mock(side_effect=[bad, changed])
    agent.notify = Mock()
    with pytest.raises(ValueError, match='unrequested paths'):
        agent.plan([{'page': 1, 'text': 'unrelated'}])


def test_whitespace_only_quote_cannot_ground(experiment):
    from paperlab.agents import CitationValidationError, verify_citations
    plan = citation_plan(experiment)
    plan.citations[0].quote = '  '
    with pytest.raises(CitationValidationError) as error:
        verify_citations(plan, [{'page': 1, 'text': 'winners'}])
    assert error.value.issues[0]['matching_pdf_pages'] == []
