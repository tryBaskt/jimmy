from fastapi.testclient import TestClient

from app.main import create_app


def test_health_contract():
    with TestClient(create_app()) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "jimmy-api"}
        assert "/api/health" in client.get("/api/openapi.json").json()["paths"]
