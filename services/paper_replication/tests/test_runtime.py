import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

import jobs
import runtime
import storage


@pytest.fixture
def objects(monkeypatch):
    objects = {}

    def put(key, data, *, create_only=False, **_):
        if create_only and key in objects:
            return False
        objects[key] = data
        return True

    monkeypatch.setattr(storage, "put", put)
    monkeypatch.setattr(storage, "get", objects.get)
    for name, value in {"ECS_CLUSTER_ARN": "cluster", "ECS_TASK_DEFINITION_ARN": "family:1",
                        "ECS_SUBNET_IDS": "subnet", "ECS_SECURITY_GROUP_ID": "sg"}.items():
        monkeypatch.setenv(name, value)
    return objects


def test_api_launches_on_demand_and_polls_and_cancels_the_same_task(objects, monkeypatch):
    calls = []
    ecs = SimpleNamespace(
        run_task=lambda **args: calls.append(args) or {"tasks": [{"taskArn": "task"}]},
        describe_tasks=lambda **_: {"tasks": [{"lastStatus": "RUNNING"}]},
        stop_task=lambda **args: calls.append(args),
    )
    monkeypatch.setattr(storage, "aws_client", lambda service: ecs)
    run_id = str(uuid4())
    submit = {"paper": b"%PDF-fixture", "filename": "paper.pdf", "options": {}}
    assert jobs.invoke(run_id, "alice", "start", **submit)["status"] == "running"
    assert jobs.invoke(run_id, "alice", "start", **submit)["status"] == "running"
    assert len(calls) == 1
    assert calls[0]["launchType"] == "FARGATE"
    assert calls[0]["count"] == 1
    assert calls[0]["overrides"]["containerOverrides"][0]["environment"] == [
        {"name": "RUN_PREFIX", "value": jobs.run_prefix(run_id, "alice")},
    ]
    with pytest.raises(HTTPException) as error:
        jobs.invoke(run_id, "bob", "cancel")
    assert error.value.status_code == 404
    assert jobs.invoke(run_id, "alice", "cancel")["status"] == "cancelled"
    assert jobs.invoke(run_id, "alice")["status"] == "cancelled"
    assert calls[1]["task"] == "task"


def test_retry_keeps_task_definition_and_idempotency_token(objects, monkeypatch):
    calls = []

    def run_task(**args):
        calls.append(args)
        return {"tasks": [{"taskArn": "task"}]} if len(calls) == 2 else {"failures": [{"reason": "CAPACITY"}]}

    monkeypatch.setattr(storage, "aws_client", lambda _: SimpleNamespace(
        run_task=run_task, describe_tasks=lambda **_: {"tasks": []}))
    args = (str(uuid4()), "alice", "start")
    submit = {"paper": b"%PDF-fixture", "filename": "paper.pdf", "options": {}}
    with pytest.raises(HTTPException) as error:
        jobs.invoke(*args, **submit)
    assert error.value.status_code == 503
    monkeypatch.setenv("ECS_TASK_DEFINITION_ARN", "family:2")
    assert jobs.invoke(*args, **submit)["status"] == "running"
    assert calls[0] == calls[1]
    assert calls[1]["taskDefinition"] == "family:1"


def test_worker_publishes_report_before_final_snapshot(objects, tmp_path, monkeypatch):
    run_id = str(uuid4())
    prefix = jobs.run_prefix(run_id, "alice")
    paper_key = f"{prefix}/uploads/paper.pdf"
    objects[paper_key] = b"%PDF-fixture"
    storage.put_json(f"{prefix}/request.json", {
        "id": run_id, "filename": "paper.pdf", "created_at": "2000-01-01T00:00:00+00:00",
        "paper_key": paper_key, "options": {"workers": 2},
    })
    monkeypatch.setattr(runtime, "configure_openai", lambda: "pi")
    monkeypatch.setattr(runtime, "chat_model", lambda name, **_: name)

    def research(config, paper_path):
        assert paper_path.read_bytes() == b"%PDF-fixture"
        assert config.pi_model == "pi"
        assert config.workers == 2
        config.event("planner", "Planning")
        assert jobs.invoke(run_id, "alice")["events"][-1]["message"] == "Planning"
        (config.run_dir / "report.md").write_text("saved report")

    monkeypatch.setattr(runtime, "run_research", research)
    runtime.run_job(prefix, tmp_path)
    assert jobs.invoke(run_id, "alice")["status"] == "completed"
    assert jobs.invoke(run_id, "alice", "artifact", artifact="report.md") == {"content": "saved report"}
    with pytest.raises(HTTPException) as error:
        jobs.invoke(run_id, "bob", "artifact", artifact="report.md")
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        jobs.invoke(run_id, "alice", "artifact", artifact="../../request.json")
    assert error.value.status_code == 400
    assert json.loads(objects[f"{prefix}/snapshot.json"])["report_ready"] is True
