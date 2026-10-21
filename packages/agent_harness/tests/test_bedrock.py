from unittest.mock import Mock

import pytest

from packages.agent_harness.bedrock import BedrockStructuredClient


TOOL = {"toolSpec": {"name": "submit_result", "inputSchema": {"json": {"type": "object"}}}}


def test_structured_call_offers_one_tool_and_sets_explicit_token_budget():
    session = Mock()
    runtime = session.client.return_value
    runtime.converse.return_value = {
        "stopReason": "tool_use",
        "output": {"message": {"content": [
            {"toolUse": {"name": "submit_result", "input": {"answer": 1}}}
        ]}},
    }
    client = BedrockStructuredClient(session=session, model_id="test-model")

    assert client.request(
        instructions="Return a result", prompt="Calculate it",
        tool=TOOL, max_tokens=128,
    ) == {"answer": 1}
    request = runtime.converse.call_args.kwargs
    assert request["modelId"] == "test-model"
    assert request["inferenceConfig"] == {"maxTokens": 128}
    assert request["toolConfig"]["toolChoice"] == {"auto": {}}


@pytest.mark.parametrize("response", [
    {"stopReason": "end_turn", "output": {"message": {"content": []}}},
    {"stopReason": "tool_use", "output": {"message": {"content": [
        {"toolUse": {"name": "other_tool", "input": {}}}
    ]}}},
    {"stopReason": "tool_use", "output": {"message": {"content": [
        {"toolUse": {"name": "submit_result", "input": {}}},
        {"toolUse": {"name": "submit_result", "input": {}}},
    ]}}},
])
def test_structured_call_rejects_missing_wrong_or_multiple_tool_uses(response):
    session = Mock()
    session.client.return_value.converse.return_value = response
    client = BedrockStructuredClient(session=session, model_id="test-model")
    with pytest.raises(ValueError, match="one valid"):
        client.request(
            instructions="Return a result", prompt="Calculate it",
            tool=TOOL, max_tokens=128,
        )
