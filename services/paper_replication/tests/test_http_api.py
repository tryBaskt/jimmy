import base64
import io
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pypdf import PdfWriter

import security.workflow_auth as auth
import paper_replication_api as main
import clients.agentcore_client as agentcore


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("COGNITO_ISSUER", "https://issuer.example/pool")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client")
    monkeypatch.delenv("JIMMY_AUTH_MODE", raising=False)
    with TestClient(main.app) as client:
        yield client


def test_routes_require_authentication(client):
    assert client.get("/api/paper-replication/health").status_code == 200
    assert client.get("/api/paper-replication/me").status_code == 401
    assert client.get(f"/api/paper-replication/runs/{uuid4()}").status_code == 401
    assert client.post("/api/paper-replication/runs").status_code == 401


def test_local_auth_cannot_bypass_deployed_auth(client, monkeypatch):
    monkeypatch.setenv("JIMMY_AUTH_MODE", "local")
    monkeypatch.setenv("JIMMY_ENV", "prod")
    assert client.get("/api/paper-replication/me").status_code == 401


@pytest.mark.parametrize("change", [{}, {"exp": 1}, {"aud": "other"}, {"iss": "other"},
                                   {"token_use": "access"}])
def test_cognito_identity_token_validation(client, monkeypatch, change):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(auth, "keys", lambda issuer: SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())))
    claims = {"sub": "user-123", "iat": int(time.time()), "exp": int(time.time()) + 60,
              "iss": "https://issuer.example/pool", "aud": "client", "token_use": "id"} | change
    token = jwt.encode(claims, key, algorithm="RS256")
    response = client.get("/api/paper-replication/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == (401 if change else 200)
    if not change:
        assert response.json() == {"sub": "user-123"}


def test_pdf_submission_passes_verified_owner_and_stable_id(client, monkeypatch):
    main.app.dependency_overrides[auth.current_user] = lambda: "verified-user"
    calls = []
    monkeypatch.setattr(main, "invoke", lambda *args, **kwargs: calls.append((args, kwargs)) or {"id": args[0]})
    writer = PdfWriter(); writer.add_blank_page(width=612, height=792)
    paper = io.BytesIO(); writer.write(paper)
    run_id = str(uuid4())
    try:
        response = client.post("/api/paper-replication/runs", files={"paper": ("paper.pdf", paper.getvalue(), "application/pdf")},
                               data={"run_id": run_id})
        assert response.status_code == 202
        assert calls[0][0] == (run_id, "verified-user", "start")
        assert base64.b64decode(calls[0][1]["paper"]) == paper.getvalue()
        response = client.post("/api/paper-replication/runs", files={"paper": ("paper.pdf", b"bad")})
        assert response.status_code == 400
    finally:
        main.app.dependency_overrides.clear()


def test_agentcore_response_stream_is_closed(monkeypatch):
    body = io.BytesIO(json.dumps({"id": "test"}).encode())
    calls = []
    monkeypatch.setenv("AGENTCORE_RUNTIME_ARN", "arn:example")
    monkeypatch.delenv("WORKFLOW_TRANSPORT", raising=False)
    monkeypatch.setattr(agentcore, "client", lambda: SimpleNamespace(
        invoke_agent_runtime=lambda **kwargs: calls.append(kwargs) or {"response": body}))
    run_id = str(uuid4())
    assert agentcore.invoke(run_id, "owner") == {"id": "test"}
    assert calls[0]["runtimeSessionId"] == run_id
    assert json.loads(calls[0]["payload"])["owner"] == "owner"
    assert body.closed
