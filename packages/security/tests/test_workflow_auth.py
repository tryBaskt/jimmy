import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from security import workflow_auth as auth


@pytest.fixture(autouse=True)
def configured_auth(monkeypatch):
    monkeypatch.delenv('JIMMY_AUTH_MODE', raising=False)
    monkeypatch.setenv('JIMMY_ENV', 'dev')
    monkeypatch.setenv('COGNITO_ISSUER', 'https://issuer.example/pool')
    monkeypatch.setenv('COGNITO_CLIENT_ID', 'expected-client')
    # Any unexpected JWKS access fails locally rather than accessing the network.
    monkeypatch.setattr(auth, 'keys', lambda issuer: pytest.fail('Unexpected JWKS lookup'))


@pytest.fixture(scope='module')
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def credentials(token):
    return HTTPAuthorizationCredentials(scheme='Bearer', credentials=token)


def test_valid_identity_token_returns_verified_subject(monkeypatch, signing_key):
    monkeypatch.setattr(auth, 'keys', lambda issuer: SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=signing_key.public_key())))
    now = int(time.time())
    token = jwt.encode({'sub': 'user-123', 'iss': 'https://issuer.example/pool',
                        'aud': 'expected-client', 'iat': now, 'exp': now + 300,
                        'token_use': 'id'}, signing_key, algorithm='RS256')
    assert auth.current_user(credentials(token)) == 'user-123'


@pytest.mark.parametrize('change,missing', [
    ({'exp': 1}, None), ({'iat': int(time.time()) + 3600}, None),
    ({'aud': 'another-client'}, None), ({'iss': 'https://wrong.example'}, None),
    ({'token_use': 'access'}, None),
    ({}, 'exp'), ({}, 'iat'), ({}, 'sub'), ({}, 'token_use'),
])
def test_invalid_claims_are_rejected(monkeypatch, signing_key, change, missing):
    monkeypatch.setattr(auth, 'keys', lambda issuer: SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=signing_key.public_key())))
    now = int(time.time())
    claims = {'sub': 'user-123', 'iss': 'https://issuer.example/pool', 'aud': 'expected-client',
              'iat': now, 'exp': now + 300, 'token_use': 'id'} | change
    if missing:
        claims.pop(missing)
    token = jwt.encode(claims, signing_key, algorithm='RS256')
    with pytest.raises(HTTPException) as error:
        auth.current_user(credentials(token))
    assert error.value.status_code == 401
    assert error.value.headers == {'WWW-Authenticate': 'Bearer'}


@pytest.mark.parametrize('token', ['malformed', jwt.encode({'sub': 'forged'}, 'wrong-secret', algorithm='HS256')])
def test_untrusted_tokens_are_rejected(monkeypatch, signing_key, token):
    monkeypatch.setattr(auth, 'keys', lambda issuer: SimpleNamespace(
        get_signing_key_from_jwt=lambda value: SimpleNamespace(key=signing_key.public_key())))
    with pytest.raises(HTTPException) as error:
        auth.current_user(credentials(token))
    assert error.value.status_code == 401


def test_jwks_failure_does_not_authenticate(monkeypatch):
    def unavailable(token):
        raise jwt.PyJWKClientError('Signing key unavailable')
    monkeypatch.setattr(auth, 'keys', lambda issuer: SimpleNamespace(get_signing_key_from_jwt=unavailable))
    with pytest.raises(HTTPException) as error:
        auth.current_user(credentials('unverified-token'))
    assert error.value.status_code == 401


def test_missing_bearer_challenges_user():
    with pytest.raises(HTTPException) as error:
        auth.current_user(None)
    assert error.value.status_code == 401
    assert error.value.headers == {'WWW-Authenticate': 'Bearer'}


@pytest.mark.parametrize('setting', ['COGNITO_ISSUER', 'COGNITO_CLIENT_ID'])
def test_missing_configuration_fails_closed(monkeypatch, setting):
    monkeypatch.delenv(setting)
    with pytest.raises(HTTPException) as error:
        auth.current_user(None)
    assert error.value.status_code == 503


@pytest.mark.parametrize('environment', ['local', 'dev', 'prod'])
def test_old_local_auth_setting_cannot_bypass_cognito(monkeypatch, environment):
    monkeypatch.setenv('JIMMY_AUTH_MODE', 'local')
    monkeypatch.setenv('JIMMY_ENV', environment)
    with pytest.raises(HTTPException) as error:
        auth.current_user(None)
    assert error.value.status_code == 401
