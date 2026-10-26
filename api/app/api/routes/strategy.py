"""AWS HTTP API for asynchronous strategy jobs."""

from __future__ import annotations

import logging
import json
import time
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi import Response
from pydantic import BaseModel, Field

from app.config import Settings
from app.api.routes.auth import current_session, require_same_origin

router = APIRouter(prefix="/strategy", tags=["strategy"])
logger = logging.getLogger(__name__)
STRATEGY_SCHEMA_VERSION = 4
GICS_SECTORS = (
    "Communication Services", "Consumer Discretionary", "Consumer Staples",
    "Energy", "Financials", "Health Care", "Industrials",
    "Information Technology", "Materials", "Real Estate", "Utilities",
)
EARLIEST_BACKTEST_DATE = date(1996, 1, 1)


class StrategyRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    lookback_months: int = Field(ge=1, le=12, strict=True)
    sectors: list[str] = Field(default_factory=list, max_length=len(GICS_SECTORS))
    start_date: date | None = None
    end_date: date | None = None


def _job_dates(body: StrategyRequest) -> tuple[date, date]:
    today = datetime.now(timezone.utc).date()
    if (body.start_date is None) != (body.end_date is None):
        raise HTTPException(status_code=422, detail="Choose both a start date and an end date")
    if body.start_date is None:
        end_date = today
        start_date = end_date - timedelta(days=365)
    else:
        start_date, end_date = body.start_date, body.end_date
    if start_date < EARLIEST_BACKTEST_DATE:
        raise HTTPException(status_code=422, detail="Start date must be on or after 1996-01-01")
    if end_date > today:
        raise HTTPException(status_code=422, detail="End date cannot be in the future")
    if end_date - start_date < timedelta(days=90):
        raise HTTPException(
            status_code=422,
            detail="The backtest must span at least 90 days",
        )
    return start_date, end_date


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
        "sectors": list(GICS_SECTORS),
        "earliest_start_date": EARLIEST_BACKTEST_DATE.isoformat(),
        "latest_end_date": datetime.now(timezone.utc).date().isoformat(),
    }


@router.post("/run")
def run(request: Request, body: StrategyRequest, response: Response) -> dict:
    settings: Settings = request.app.state.settings
    _require_job_configuration(settings)
    import boto3

    require_same_origin(request)
    user = current_session(request)
    unknown_sectors = sorted(set(body.sectors) - set(GICS_SECTORS))
    if unknown_sectors:
        raise HTTPException(status_code=422, detail=f"Unknown GICS sector: {unknown_sectors[0]}")
    if len(set(body.sectors)) != len(body.sectors):
        raise HTTPException(status_code=422, detail="Each GICS sector may be selected once")
    start_date, end_date = _job_dates(body)
    session = boto3.Session(region_name=settings.aws_region)
    jobs = session.resource("dynamodb").Table(settings.jobs_table)
    queue = session.client("sqs")
    job_id = str(uuid.uuid4())
    jobs.put_item(Item={
        "job_id": job_id,
        "user_sub": user["user_sub"],
        "prompt": body.prompt.strip(),
        "lookback_months": body.lookback_months,
        "sectors": sorted(body.sectors),
        "start_at": datetime.combine(start_date, datetime.min.time(), timezone.utc).isoformat(),
        "end_at": datetime.combine(end_date + timedelta(days=1), datetime.min.time(), timezone.utc).isoformat(),
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
