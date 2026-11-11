"""Start, inspect, and stop a paper replication task from the authenticated API."""

import csv
import hashlib
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import storage
from harness.models import MAX_DATA_ATTEMPTS


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extrapolations: int = Field(3, ge=0, le=5)
    data_attempts: int = Field(MAX_DATA_ATTEMPTS, ge=1, le=MAX_DATA_ATTEMPTS)
    workers: int = Field(4, ge=1, le=8)
    symbols: str = Field("", max_length=1000, pattern=r"^[A-Za-z0-9.,^= _-]*$")


def run_prefix(run_id, owner):
    owner_key = hashlib.sha256(owner.encode()).hexdigest()
    return f"runs/{owner_key}/{run_id}"


def start(prefix, run_id, paper, filename, options):
    request_key = f"{prefix}/request.json"
    job = storage.get_json(request_key)
    if job is None:
        try:
            options = Options.model_validate(options).model_dump()
        except ValidationError as error:
            raise HTTPException(422, "Invalid workflow options") from error
        paper_key = f"{prefix}/uploads/{hashlib.sha256(paper).hexdigest()}.pdf"
        storage.put(paper_key, paper, content_type="application/pdf")
        job = {
            "id": run_id, "filename": Path(filename).name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "paper_key": paper_key, "options": options,
            "launch": {
                "cluster": os.environ["ECS_CLUSTER_ARN"], "taskDefinition": os.environ["ECS_TASK_DEFINITION_ARN"],
                "launchType": "FARGATE",
                "networkConfiguration": {"awsvpcConfiguration": {
                    "subnets": os.environ["ECS_SUBNET_IDS"].split(","),
                    "securityGroups": [os.environ["ECS_SECURITY_GROUP_ID"]], "assignPublicIp": "ENABLED",
                }},
            },
        }
        if not storage.put_json(request_key, job, create_only=True):
            job = storage.get_json(request_key)

    task_key = f"{prefix}/task.json"
    task = storage.get_json(task_key)
    if task is None:
        # ECS deduplicates concurrent launches. Keep the original task definition
        # and network settings across retries, even if a deployment intervenes.
        if datetime.now(timezone.utc) - datetime.fromisoformat(job["created_at"]) > timedelta(minutes=15):
            raise HTTPException(409, "The launch window expired. Start a new run.")
        result = storage.aws_client("ecs").run_task(
            **job["launch"], count=1, clientToken=hashlib.sha256(prefix.encode()).hexdigest(),
            overrides={"containerOverrides": [{"name": "worker", "environment": [
                {"name": "RUN_PREFIX", "value": prefix},
            ]}]},
        )
        if not result.get("tasks"):
            raise HTTPException(503, "ECS could not start the task. Retry with the same run ID.")
        task = {"arn": result["tasks"][0]["taskArn"]}
        storage.put_json(task_key, task, create_only=True)
    return snapshot(prefix, job, task)


def snapshot(prefix, job, task):
    state = storage.get_json(f"{prefix}/snapshot.json")
    if state is None:
        state = {
            "id": job["id"], "status": "running", "created_at": job["created_at"],
            "filename": job["filename"], "title": job["filename"], "report_ready": False,
            "completed_experiments": 0, "failed_experiments": 0, "plan": None,
            "results": [], "events": [], "experiments": [], "acquisitions": [], "decisions": [], "web": [],
        }
    if storage.get_json(f"{prefix}/cancelled.json"):
        return state | {"status": "cancelled"}
    if state["status"] == "running" and task:
        tasks = storage.aws_client("ecs").describe_tasks(cluster=job["launch"]["cluster"], tasks=[task["arn"]])
        stopped = any(item["lastStatus"] == "STOPPED" for item in tasks.get("tasks", []))
        missing = any(item.get("reason") == "MISSING" for item in tasks.get("failures", []))
        if stopped or (missing and datetime.now(timezone.utc) - datetime.fromisoformat(job["created_at"])
                       > timedelta(minutes=15)):
            # Read again so a snapshot published between the first read and the
            # task stopping still wins over the infrastructure failure fallback.
            final = storage.get_json(f"{prefix}/snapshot.json")
            if final and final["status"] != "running":
                return final
            return state | {"status": "failed", "error": "The ECS task stopped before saving a final result."}
    if state["status"] == "running" and not task and (
        datetime.now(timezone.utc) - datetime.fromisoformat(job["created_at"]) > timedelta(minutes=15)
    ):
        return state | {"status": "failed", "error": "ECS did not start the task within the launch window."}
    return state


def invoke(run_id, owner, action="snapshot", **arguments):
    prefix = run_prefix(run_id, owner)
    try:
        if action == "start":
            return start(prefix, run_id, **arguments)
        job = storage.get_json(f"{prefix}/request.json")
        if job is None:
            raise HTTPException(404, "Run not found")
        if action in {"snapshot", "cancel"}:
            task = storage.get_json(f"{prefix}/task.json")
            state = snapshot(prefix, job, task)
            if action == "snapshot" or state["status"] == "cancelled":
                return state
            if state["status"] != "running" or not task:
                raise HTTPException(409, "Run has no active task")
            storage.aws_client("ecs").stop_task(cluster=job["launch"]["cluster"], task=task["arn"],
                                                reason="Cancelled by the run owner")
            storage.put_json(f"{prefix}/cancelled.json", {"cancelled": True})
            return state | {"status": "cancelled"}
        if action == "artifact" and arguments["artifact"] in {"report.md", "results.json", "plan.json"}:
            name = arguments["artifact"]
        elif action == "series" and re.fullmatch(r"[a-z][a-z0-9_]{0,39}", arguments["experiment"]):
            name = f"experiments/{arguments['experiment']}/returns.csv"
        else:
            raise HTTPException(400, "Invalid run action or artifact")
        data = storage.get(f"{prefix}/artifacts/{name}")
        if data is None:
            raise HTTPException(404, "Artifact not available")
        if action == "artifact":
            return {"content": data.decode()}
        rows = []
        for row in csv.DictReader(data.decode().splitlines()):
            values = {key: float(row.get(key, "nan")) for key in ("gross_return", "estimated_net_return", "turnover")}
            rows.append({"date": row["date"], **{key: val if math.isfinite(val) else None for key, val in values.items()}})
        return {"rows": rows}
    except (BotoCoreError, ClientError) as error:
        raise HTTPException(502, "Workflow storage or ECS is unavailable. Retry with the same run ID.") from error
