from fastapi.testclient import TestClient

from app.api.routes import research
from app.config import Settings
from app.main import create_app


def test_research_is_disabled_by_default():
    with TestClient(create_app(Settings(local_research_enabled=False))) as client:
        assert client.get("/api/research/capabilities").json() == {"enabled": False, "schema_version": 2}
        response = client.post("/api/research/run", json={"prompt": "Momentum", "lookback_months": 3})
        assert response.status_code == 503


def test_local_research_forwards_prompt_and_returns_result(monkeypatch):
    calls = []

    def fake_execute(prompt, lookback_months, settings):
        calls.append((prompt, lookback_months, settings.local_research_enabled))
        return {"plan": {"lookback_months": 3}, "eligible_symbol_count": 196}

    monkeypatch.setattr(research, "execute_research", fake_execute)
    with TestClient(create_app(Settings(local_research_enabled=True))) as client:
        assert client.get("/api/research/capabilities").json() == {"enabled": True, "schema_version": 2}
        response = client.post("/api/research/run", json={"prompt": "  Momentum  ", "lookback_months": 3})
        assert response.status_code == 200
        assert response.json()["schema_version"] == 2
        assert response.json()["plan"]["lookback_months"] == 3
        assert calls == [("Momentum", 3, True)]
        assert client.post("/api/research/run", json={"prompt": "", "lookback_months": 3}).status_code == 422
        assert client.post("/api/research/run", json={"prompt": "Momentum", "lookback_months": 0}).status_code == 422
