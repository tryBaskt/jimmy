from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.api.routes import auth


def test_cloud_jobs_require_a_session_and_same_origin():
    settings = Settings(
        cognito_domain="https://example.auth.us-east-1.amazoncognito.com",
        cognito_client_id="client", public_url="https://app.example.com",
        sessions_table="sessions", jobs_table="jobs", jobs_queue_url="queue",
        results_bucket="results",
    )
    client = TestClient(create_app(settings))

    assert client.get("/api/auth/session").status_code == 401
    assert client.post("/api/strategy/run", json={"prompt": "Buy momentum", "lookback_months": 3}).status_code == 403
    assert client.post(
        "/api/strategy/run", json={"prompt": "Buy momentum", "lookback_months": 3},
        headers={"Origin": "https://app.example.com"},
    ).status_code == 401
    assert client.get("/api/strategy/jobs/00000000-0000-0000-0000-000000000000").status_code == 401


def test_cognito_callback_creates_opaque_cookie_and_server_side_tokens(monkeypatch):
    settings = Settings(
        cognito_domain="https://example.auth.us-east-1.amazoncognito.com",
        cognito_client_id="client", public_url="https://app.example.com",
        sessions_table="sessions",
    )

    class Table:
        def __init__(self):
            self.items = {}

        def put_item(self, Item):
            self.items[Item["session_id"]] = Item

        def get_item(self, Key, **kwargs):
            return {"Item": self.items.get(Key["session_id"])}

        def delete_item(self, Key, **kwargs):
            self.items.pop(Key["session_id"], None)

    table = Table()
    monkeypatch.setattr(auth, "_table", lambda _settings: table)
    monkeypatch.setattr(auth, "_cognito_form", lambda *_args: {
        "access_token": "access", "refresh_token": "refresh", "expires_in": 3600,
    })
    monkeypatch.setattr(auth, "_userinfo", lambda *_args: {"sub": "user-1", "email": "a@example.com"})
    client = TestClient(create_app(settings), base_url="https://app.example.com", follow_redirects=False)

    login = client.get("/api/auth/login")
    assert login.status_code == 302
    state = client.cookies[auth.STATE_COOKIE]
    assert "code_challenge_method=S256" in login.headers["location"]
    callback = client.get("/api/auth/callback", params={"code": "code", "state": state})
    assert callback.status_code == 303
    assert "httponly" in callback.headers["set-cookie"].lower()
    assert "refresh" not in callback.headers["set-cookie"]
    assert any(item.get("refresh_token") == "refresh" for item in table.items.values())
