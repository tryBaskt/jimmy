"""AWS HTTP API for asynchronous strategy jobs."""

from __future__ import annotations

import logging
import json
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi import Response
from pydantic import BaseModel, Field

from app.config import Settings
from app.api.routes.auth import current_session, require_same_origin

router = APIRouter(prefix="/strategy", tags=["strategy"])
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
STRATEGY_SCHEMA_VERSION = 3


class StrategyRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    lookback_months: int = Field(ge=1, le=12, strict=True)


def _require_job_configuration(settings: Settings) -> None:
    if not all((settings.jobs_queue_url, settings.jobs_table, settings.results_bucket,
                settings.cognito_domain, settings.cognito_client_id,
                settings.public_url, settings.sessions_table)):
        raise HTTPException(status_code=503, detail="Strategy jobs are not configured")


@router.get("/capabilities")
def capabilities(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "enabled": all((settings.jobs_queue_url, settings.jobs_table, settings.results_bucket,
                        settings.cognito_domain, settings.cognito_client_id,
                        settings.public_url, settings.sessions_table)),
        "schema_version": STRATEGY_SCHEMA_VERSION,
    }


@router.post("/run")
def run(request: Request, body: StrategyRequest, response: Response) -> dict:
    settings: Settings = request.app.state.settings
    _require_job_configuration(settings)
    import boto3

    require_same_origin(request)
    user = current_session(request)
    session = boto3.Session(region_name=settings.aws_region)
    jobs = session.resource("dynamodb").Table(settings.jobs_table)
    queue = session.client("sqs")
    job_id = str(uuid.uuid4())
    jobs.put_item(Item={
        "job_id": job_id,
        "user_sub": user["user_sub"],
        "prompt": body.prompt.strip(),
        "lookback_months": body.lookback_months,
        "end_at": datetime.now(timezone.utc).isoformat(),
        "status": "queued",
        "created_at": int(time.time()),
        "expires_at": int(time.time()) + 30 * 24 * 60 * 60,
    })
    try:
        queue.send_message(QueueUrl=settings.jobs_queue_url, MessageBody=json.dumps({"job_id": job_id}))
    except Exception as error:
        logger.exception("Could not enqueue strategy job %s", job_id)
        jobs.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #status = :failed, #error = :error",
            ExpressionAttributeNames={"#status": "status", "#error": "error"},
            ExpressionAttributeValues={
                ":failed": "failed", ":error": "Could not queue strategy run",
            },
        )
        raise HTTPException(status_code=502, detail="Could not queue strategy run") from error
    logger.info("Queued strategy job %s lookback_months=%s", job_id, body.lookback_months)
    response.status_code = 202
    return {"job_id": job_id, "status": "queued"}


@router.get("/jobs/{job_id}")
def get_job(request: Request, job_id: uuid.UUID) -> dict:
    import boto3

    settings: Settings = request.app.state.settings
    _require_job_configuration(settings)
    user = current_session(request)
    session = boto3.Session(region_name=settings.aws_region)
    item = session.resource("dynamodb").Table(settings.jobs_table).get_item(
        Key={"job_id": str(job_id)}, ConsistentRead=True,
    ).get("Item")
    if not item or item["user_sub"] != user["user_sub"] or item["expires_at"] <= int(time.time()):
        raise HTTPException(status_code=404, detail="Job not found")
    status = item["status"]
    if status == "complete":
        body = session.client("s3").get_object(
            Bucket=settings.results_bucket, Key=f"jobs/{job_id}.json",
        )["Body"].read()
        return {"job_id": str(job_id), "status": status, "result": json.loads(body)}
    if status == "failed":
        return {"job_id": str(job_id), "status": status,
                "error": item.get("error", "Strategy run failed")}
    return {"job_id": str(job_id), "status": status}
