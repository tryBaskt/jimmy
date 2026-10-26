import json
from datetime import datetime, timezone

import boto3
from fastapi.testclient import TestClient

from app.api.routes import strategy
from app.config import Settings
from app.main import create_app


def test_strategy_requires_aws_job_configuration():
    with TestClient(create_app(Settings())) as client:
        capabilities = client.get("/api/strategy/capabilities").json()
        assert capabilities["enabled"] is False
        assert capabilities["schema_version"] == 4
        assert capabilities["earliest_start_date"] == "1996-01-01"
        assert capabilities["latest_end_date"] == datetime.now(timezone.utc).date().isoformat()
        assert "Information Technology" in capabilities["sectors"]
        response = client.post(
            "/api/strategy/run", json={"prompt": "Buy prior winners", "lookback_months": 3}
        )
        assert response.status_code == 503
        assert client.get("/api/research/capabilities").status_code == 404


def test_strategy_only_enqueues_authenticated_aws_job(monkeypatch):
    settings = Settings(
        cognito_domain="https://example.auth.us-east-1.amazoncognito.com",
        cognito_client_id="client", public_url="https://app.example.com",
        sessions_table="sessions", jobs_table="jobs", jobs_queue_url="queue",
        results_bucket="results",
    )
    saved = []
    sent = []

    class Table:
        def put_item(self, Item):
            saved.append(Item)

    class Queue:
        def send_message(self, **kwargs):
            sent.append(kwargs)

    class Session:
        def __init__(self, **kwargs):
            assert kwargs == {"region_name": "us-east-1"}

        def resource(self, name):
            assert name == "dynamodb"
            return self

        def Table(self, name):
            assert name == "jobs"
            return Table()

        def client(self, name):
            assert name == "sqs"
            return Queue()

    monkeypatch.setattr(boto3, "Session", Session)
    monkeypatch.setattr(strategy, "current_session", lambda _request: {"user_sub": "user-1"})
    with TestClient(create_app(settings), base_url="https://app.example.com") as client:
        assert client.get("/api/strategy/capabilities").json()["enabled"] is True
        response = client.post(
            "/api/strategy/run", json={
                "prompt": "  Buy prior winners  ", "lookback_months": 3,
                "sectors": ["Energy", "Information Technology"],
                "start_date": "2024-01-01", "end_date": "2025-01-01",
            },
            headers={"Origin": "https://app.example.com"},
        )
        assert response.status_code == 202
        assert saved[0]["prompt"] == "Buy prior winners"
        assert saved[0]["user_sub"] == "user-1"
        assert saved[0]["sectors"] == ["Energy", "Information Technology"]
        assert saved[0]["start_at"] == "2024-01-01T00:00:00+00:00"
        assert saved[0]["end_at"] == "2025-01-02T00:00:00+00:00"
        assert sent == [{"QueueUrl": "queue", "MessageBody": json.dumps({"job_id": response.json()["job_id"]})}]
        assert client.post(
            "/api/strategy/run", json={"prompt": "", "lookback_months": 3},
        ).status_code == 422


def test_strategy_rejects_invalid_filters_before_aws(monkeypatch):
    settings = Settings(
        cognito_domain="https://example.auth.us-east-1.amazoncognito.com",
        cognito_client_id="client", public_url="https://app.example.com",
        sessions_table="sessions", jobs_table="jobs", jobs_queue_url="queue",
        results_bucket="results",
    )
    monkeypatch.setattr(strategy, "current_session", lambda _request: {"user_sub": "user-1"})
    with TestClient(create_app(settings), base_url="https://app.example.com") as client:
        headers = {"Origin": "https://app.example.com"}
        assert client.post("/api/strategy/run", headers=headers, json={
            "prompt": "Buy winners", "lookback_months": 3, "sectors": ["Technology"],
            "start_date": "2024-01-01", "end_date": "2025-01-01",
        }).status_code == 422
        response = client.post("/api/strategy/run", headers=headers, json={
            "prompt": "Buy winners", "lookback_months": 3,
            "start_date": "2024-01-01", "end_date": "2024-02-01",
        })
        assert response.status_code == 422
        assert response.json()["detail"] == "The backtest must span at least 90 days"
