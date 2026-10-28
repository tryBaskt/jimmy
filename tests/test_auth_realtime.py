from dataclasses import replace
from types import SimpleNamespace
import time
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from apis.app import create_app
from paperlab.storage import digest


def test_cognito_identity_fail_closed(settings, store, monkeypatch):
    from apis import context

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = replace(settings, mode="aws", issuer="https://issuer.example", audience="client")
    monkeypatch.setattr(
        context,
        "jwks",
        lambda _: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private.public_key())
        ),
    )
    c = TestClient(create_app(settings, store, "identity"))
    claims = {
        "sub": "u",
        "iss": settings.issuer,
        "aud": settings.audience,
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "token_use": "id",
    }

    def request(overrides):
        token = jwt.encode({**claims, **overrides}, private, algorithm="RS256")
        return c.get("/api/identity/me", headers={"Authorization": "Bearer " + token})

    assert request({}).status_code == 200
    assert request({"token_use": "access"}).status_code == 401
    assert request({"aud": "wrong"}).status_code == 401
    assert request({"exp": int(time.time()) - 60}).status_code == 401
    assert c.get("/api/identity/me").status_code == 401


def test_websocket_ticket_single_use_and_project_ownership(settings, store, monkeypatch):
    import paperlab.realtime as live

    monkeypatch.setattr(live.Settings, "load", lambda: settings)
    monkeypatch.setattr(live, "make_store", lambda _: store)
    token = "single-use-test-ticket"
    store.put(
        "tickets",
        digest(token.encode()),
        {"owner": "u", "until": time.time() + 60, "session_until": time.time() + 3600},
    )
    store.put("project#own", "meta", {"owner": "u"})
    store.put("project#other", "meta", {"owner": "someone-else"})

    def event(route, **kwargs):
        return {"requestContext": {"routeKey": route, "connectionId": "conn"}, **kwargs}

    connect = event("$connect", queryStringParameters={"ticket": token})
    assert live.connection_handler(connect, None)["statusCode"] == 200
    assert live.connection_handler(connect, None)["statusCode"] == 401
    assert (
        live.connection_handler(event("subscribe", body='{"project_id":"other"}'), None)["statusCode"] == 403
    )
    assert live.connection_handler(event("subscribe", body='{"project_id":"own"}'), None)["statusCode"] == 200
    live.connection_handler(event("$disconnect"), None)
    assert live.connection_handler(event("subscribe", body='{"project_id":"own"}'), None)["statusCode"] == 403
