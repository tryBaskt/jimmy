import hashlib
from uuid import uuid4

import boto3
import pytest
from botocore.stub import Stubber
from fastapi import HTTPException
from fastapi.testclient import TestClient

import jobs
import paper_replication_api as api
import security.workflow_auth as auth
import storage


def test_history_query_keeps_cursor_in_authenticated_owner_partition(monkeypatch):
    client = boto3.client('dynamodb', region_name='us-east-1',
                          aws_access_key_id='test', aws_secret_access_key='test')
    monkeypatch.setenv('RUN_TABLE', 'history')
    monkeypatch.setattr(storage, 'aws_client', lambda _: client)
    with Stubber(client) as stub:
        stub.add_response('query', {'Items': [{'run_id': {'S': 'run-1'}}],
                                   'LastEvaluatedKey': {'owner': {'S': 'alice'}, 'created_run': {'S': 'next'}}}, {
            'TableName': 'history', 'KeyConditionExpression': '#owner = :owner',
            'ExpressionAttributeNames': {'#owner': 'owner'},
            'ExpressionAttributeValues': {':owner': {'S': 'alice'}},
            'ScanIndexForward': False, 'ConsistentRead': True, 'Limit': 20,
            'ExclusiveStartKey': {'owner': {'S': 'alice'}, 'created_run': {'S': 'untrusted-cursor'}},
        })
        assert storage.run_page('alice', 'untrusted-cursor') == (['run-1'], 'next')
        stub.assert_no_pending_responses()


def test_history_reads_current_run_state_and_returns_small_summaries(monkeypatch):
    owner = 'cognito-sub'
    def page(owner_key, cursor, limit):
        assert owner_key == hashlib.sha256(owner.encode()).hexdigest()
        assert (cursor, limit) == ('cursor', 10)
        return ['second', 'first'], 'older'
    monkeypatch.setattr(storage, 'run_page', page)
    def invoke(run_id, user):
        assert user == owner
        return {'id': run_id, 'status': 'completed', 'results': ['large-result']}
    monkeypatch.setattr(jobs, 'invoke', invoke)
    result = jobs.history(owner, 'cursor', 10)
    assert [item['id'] for item in result['items']] == ['second', 'first']
    assert result['next_cursor'] == 'older'
    assert all(item['status'] == 'completed' and 'results' not in item for item in result['items'])


def test_history_and_paper_require_authentication_and_validate_pagination(monkeypatch):
    monkeypatch.setenv("COGNITO_ISSUER", "https://issuer.example/pool")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client")
    with TestClient(api.app) as client:
        assert client.get('/api/paper-replication/runs').status_code == 401
        assert client.get(f'/api/paper-replication/runs/{uuid4()}/paper').status_code == 401
        api.app.dependency_overrides[auth.current_user] = lambda: 'alice'
        try:
            assert client.get('/api/paper-replication/runs?limit=1000').status_code == 422
        finally:
            api.app.dependency_overrides.clear()


def test_original_pdf_is_owned_and_preserved_without_decoding(monkeypatch):
    run_id = str(uuid4())
    prefix = jobs.run_prefix(run_id, 'alice')
    pdf = b'%PDF-1.7\n\xff\x00'
    objects = {f'{prefix}/request.json': b'{"paper_key":"original"}', 'original': pdf}
    monkeypatch.setattr(storage, 'get', objects.get)
    assert jobs.invoke(run_id, 'alice', 'paper')['content'] == pdf
    with pytest.raises(HTTPException) as error:
        jobs.invoke(run_id, 'bob', 'paper')
    assert error.value.status_code == 404
