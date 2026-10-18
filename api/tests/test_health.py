from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health_contract():
    with TestClient(create_app()) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "jimmy-api"}
        assert "/api/health" in client.get("/api/openapi.json").json()["paths"]


def test_cors_only_allows_configured_origin():
    with TestClient(
        create_app(Settings(cors_origins=("http://localhost:5173",)))
    ) as client:
        allowed = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
        denied = client.get("/api/health", headers={"Origin": "https://other.example"})
        assert "access-control-allow-origin" not in denied.headers
