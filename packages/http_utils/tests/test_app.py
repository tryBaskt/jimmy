import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from http_utils.app import create_app
from security.workflow_auth import current_user


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in ['JIMMY_AUTH_MODE', 'WORKFLOW_TRANSPORT', 'AGENTCORE_RUNTIME_ARN', 'OPENAI_MODEL', 'WEB_ORIGINS']:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('JIMMY_ENV', 'dev')
    monkeypatch.setenv('COGNITO_ISSUER', 'https://issuer.example/pool')
    monkeypatch.setenv('COGNITO_CLIENT_ID', 'client')


def app(workflow_id='first_flow', runtime_configured=None):
    router = APIRouter()
    router.add_api_route('/result', lambda: {'workflow': workflow_id})
    router.add_api_route('/submit', lambda: {'ok': True}, methods=['POST'])
    return create_app(router, workflow_id=workflow_id, title='Example workflow',
                      runtime_configured=runtime_configured)


def test_health_is_public_but_identity_and_business_routes_require_auth():
    with TestClient(app()) as client:
        assert client.get('/api/first-flow/health').json() == {
            'status': 'ok', 'workflow': 'first_flow', 'runtime_configured': False, 'model': ''}
        for response in [client.get('/api/first-flow/me'), client.get('/api/first-flow/result'),
                         client.post('/api/first-flow/submit')]:
            assert response.status_code == 401
            assert response.headers['www-authenticate'] == 'Bearer'


def test_health_reports_runtime_configuration(monkeypatch):
    monkeypatch.setenv('OPENAI_MODEL', 'test-model')
    with TestClient(app(runtime_configured=lambda: True)) as client:
        health = client.get('/api/first-flow/health').json()
        assert health['runtime_configured'] is True
        assert health['model'] == 'test-model'


def test_workflow_routes_are_isolated():
    first_app, second_app = app('first_flow'), app('second_flow')
    # Test-only overrides substitute for JWT verification; production always uses Cognito.
    first_app.dependency_overrides[current_user] = lambda: 'verified-user'
    second_app.dependency_overrides[current_user] = lambda: 'verified-user'
    with TestClient(first_app) as first, TestClient(second_app) as second:
        assert first.get('/api/first-flow/result').json() == {'workflow': 'first_flow'}
        assert second.get('/api/second-flow/result').json() == {'workflow': 'second_flow'}
        assert first.get('/api/second-flow/result').status_code == 404
        assert second.get('/api/first-flow/result').status_code == 404
        assert first.get('/api/first-flow/me').json() == {'sub': 'verified-user'}


@pytest.mark.parametrize('workflow_id', ['', '../escape', 'UPPER', 'has-dash', 'a' * 33, '1invalid'])
def test_invalid_workflow_ids_are_rejected(workflow_id):
    with pytest.raises(ValueError, match='Invalid workflow ID'):
        app(workflow_id)


def test_cors_allows_configured_origin_and_authorization_header(monkeypatch):
    monkeypatch.setenv('WEB_ORIGINS', 'https://web.example,https://other.example')
    with TestClient(app()) as client:
        response = client.options('/api/first-flow/submit', headers={
            'Origin': 'https://web.example', 'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'Authorization,Content-Type'})
        assert response.status_code == 200
        assert response.headers['access-control-allow-origin'] == 'https://web.example'
        # Browsers must be able to read report download filenames.
        response = client.get('/api/first-flow/health', headers={'Origin': 'https://other.example'})
        assert 'Content-Disposition' in response.headers['access-control-expose-headers']
        response = client.options('/api/first-flow/submit', headers={
            'Origin': 'https://untrusted.example', 'Access-Control-Request-Method': 'POST'})
        assert response.status_code == 400
        assert 'access-control-allow-origin' not in response.headers


def test_localhost_is_not_allowed_by_default():
    with TestClient(app()) as client:
        response = client.options('/api/first-flow/submit', headers={
            'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'POST'})
        assert response.status_code == 400
        assert 'access-control-allow-origin' not in response.headers
