import io
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import HTTPException

from clients import agentcore_client as agentcore


@pytest.fixture(autouse=True)
def isolated_transport(monkeypatch):
    for name in ['WORKFLOW_TRANSPORT', 'WORKFLOW_RUNTIME_URL', 'AGENTCORE_RUNTIME_ARN', 'AGENTCORE_ENDPOINT']:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('JIMMY_ENV', 'local')
    monkeypatch.setattr(agentcore, 'client', lambda: pytest.fail('Unexpected AWS client access'))


def test_aws_invocation_preserves_session_owner_and_closes_stream(monkeypatch):
    run_id = str(uuid4())
    body = io.BytesIO(b'{"status":"running"}')
    invoke = Mock(return_value={'response': body})
    monkeypatch.setenv('AGENTCORE_RUNTIME_ARN', 'arn:example:runtime')
    monkeypatch.setenv('AGENTCORE_ENDPOINT', 'VERSION_1')
    monkeypatch.setattr(agentcore, 'client', lambda: SimpleNamespace(invoke_agent_runtime=invoke))
    assert agentcore.invoke(run_id, 'verified-user', 'start', prompt='Test') == {'status': 'running'}
    call = invoke.call_args.kwargs
    assert call['agentRuntimeArn'] == 'arn:example:runtime'
    assert call['runtimeSessionId'] == run_id
    assert call['qualifier'] == 'VERSION_1'
    assert call['contentType'] == call['accept'] == 'application/json'
    assert json.loads(call['payload']) == {'run_id': run_id, 'owner': 'verified-user', 'action': 'start', 'prompt': 'Test'}
    assert body.closed


def test_old_local_transport_setting_cannot_bypass_aws_configuration(monkeypatch):
    monkeypatch.setenv('WORKFLOW_TRANSPORT', 'local')
    monkeypatch.setenv('WORKFLOW_RUNTIME_URL', 'http://unused:8080')
    with pytest.raises(HTTPException) as error:
        agentcore.invoke('session', 'owner')
    assert error.value.status_code == 503


def test_missing_runtime_configuration_fails_before_aws_call():
    with pytest.raises(HTTPException) as error:
        agentcore.invoke('session', 'owner')
    assert error.value.status_code == 503


@pytest.mark.parametrize('failure', [
    ClientError({'Error': {'Code': 'ThrottlingException', 'Message': 'internal details'}}, 'InvokeAgentRuntime'),
    EndpointConnectionError(endpoint_url='https://private.example'),
])
def test_aws_failures_become_retryable_gateway_errors(monkeypatch, failure):
    monkeypatch.setenv('AGENTCORE_RUNTIME_ARN', 'arn:example:runtime')
    invoke = Mock(side_effect=failure)
    monkeypatch.setattr(agentcore, 'client', lambda: SimpleNamespace(invoke_agent_runtime=invoke))
    with pytest.raises(HTTPException) as error:
        agentcore.invoke('same-session', 'owner')
    assert error.value.status_code == 502
    assert 'same run ID' in error.value.detail
    assert 'internal details' not in error.value.detail
    assert 'private.example' not in error.value.detail
    assert invoke.call_count == 1


@pytest.mark.parametrize('status', [400, 404, 409])
def test_runtime_application_errors_preserve_status_and_close_stream(monkeypatch, status):
    body = io.BytesIO(json.dumps({'status_code': status, 'error': 'Run unavailable'}).encode())
    monkeypatch.setenv('AGENTCORE_RUNTIME_ARN', 'arn:example:runtime')
    monkeypatch.setattr(agentcore, 'client', lambda: SimpleNamespace(
        invoke_agent_runtime=lambda **kw: {'response': body}))
    with pytest.raises(HTTPException) as error:
        agentcore.invoke('session', 'owner')
    assert error.value.status_code == status
    assert error.value.detail == 'Run unavailable'
    assert body.closed
