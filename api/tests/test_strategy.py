import json

import boto3
from fastapi.testclient import TestClient

from app.api.routes import strategy
from app.config import Settings
from app.main import create_app


def test_strategy_requires_aws_job_configuration():
    with TestClient(create_app(Settings())) as client:
        assert client.get("/api/strategy/capabilities").json() == {
            "enabled": False, "schema_version": 3,
        }
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
        assert client.get("/api/strategy/capabilities").json() == {
            "enabled": True, "schema_version": 3,
        }
        response = client.post(
            "/api/strategy/run", json={"prompt": "  Buy prior winners  ", "lookback_months": 3},
            headers={"Origin": "https://app.example.com"},
        )
        assert response.status_code == 202
        assert saved[0]["prompt"] == "Buy prior winners"
        assert saved[0]["user_sub"] == "user-1"
        assert sent == [{"QueueUrl": "queue", "MessageBody": json.dumps({"job_id": response.json()["job_id"]})}]
        assert client.post(
            "/api/strategy/run", json={"prompt": "", "lookback_months": 3},
        ).status_code == 422
