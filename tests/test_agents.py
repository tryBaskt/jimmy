from io import BytesIO
import json
from unittest.mock import Mock

from pydantic import BaseModel
import pytest
from paperlab.agents import BedrockAgents
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
