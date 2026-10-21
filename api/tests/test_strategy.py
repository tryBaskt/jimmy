from fastapi.testclient import TestClient

from app.api.routes import strategy
from app.config import Settings
from app.main import create_app


def test_strategy_is_disabled_by_default():
    with TestClient(create_app(Settings(local_strategy_enabled=False))) as client:
        assert client.get("/api/strategy/capabilities").json() == {
            "enabled": False, "schema_version": 3,
        }
        response = client.post(
            "/api/strategy/run", json={"prompt": "Buy prior winners", "lookback_months": 3}
        )
        assert response.status_code == 503
        assert client.get("/api/research/capabilities").status_code == 404


def test_local_strategy_forwards_prompt_and_returns_result(monkeypatch):
    calls = []

    def fake_execute(prompt, lookback_months, settings):
        calls.append((prompt, lookback_months, settings.local_strategy_enabled))
        return {"plan": {"lookback_months": 3}, "eligible_symbol_count": 196}

    monkeypatch.setattr(strategy, "execute_strategy", fake_execute)
    with TestClient(create_app(Settings(local_strategy_enabled=True))) as client:
        assert client.get("/api/strategy/capabilities").json() == {
            "enabled": True, "schema_version": 3,
        }
        response = client.post(
            "/api/strategy/run", json={"prompt": "  Buy prior winners  ", "lookback_months": 3}
        )
        assert response.status_code == 200
        assert response.json()["schema_version"] == 3
        assert calls == [("Buy prior winners", 3, True)]
        assert client.post(
            "/api/strategy/run", json={"prompt": "", "lookback_months": 3}
        ).status_code == 422
