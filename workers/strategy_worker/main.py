"""Consume strategy jobs from SQS and save reproducible reports in S3."""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

from packages.agents.strategy_agent.plan import BedrockStrategyAgent
from packages.agents.strategy_agent.tools.dynamodb import list_universe_symbols
from packages.agents.strategy_agent.tools.yfinance import (
    get_adjusted_close_prices,
    get_daily_prices_and_market_caps,
)
from packages.agents.strategy_agent.workflow import run_strategy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
stopping = threading.Event()


def _stop(_number, _frame) -> None:
    stopping.set()


def _visibility_heartbeat(sqs, queue_url: str, receipt: str, job_id: str, jobs,
                          done: threading.Event) -> None:
    while not done.wait(600) and not stopping.is_set():
        try:
            sqs.change_message_visibility(
                QueueUrl=queue_url, ReceiptHandle=receipt, VisibilityTimeout=3600,
            )
            jobs.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET lease_until = :lease",
                ConditionExpression="#status = :running",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":lease": int(time.time()) + 3600, ":running": "running"},
            )
        except ClientError:
            logger.exception("Could not renew job lease for %s", job_id)
            return


def process_message(message: dict, *, session, jobs, sqs, queue_url: str,
                    results_bucket: str, model_id: str) -> None:
    receipt = message["ReceiptHandle"]
    job_id = json.loads(message["Body"])["job_id"]
    attempt = int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
    now = int(time.time())
    try:
        jobs.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #status = :running, lease_until = :lease, attempts = :attempt",
            ConditionExpression="#status IN (:queued, :retrying) OR (#status = :running AND lease_until < :now)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":running": "running", ":queued": "queued", ":retrying": "retrying",
                ":lease": now + 3600, ":now": now, ":attempt": attempt,
            },
        )
    except jobs.meta.client.exceptions.ConditionalCheckFailedException:
        existing = jobs.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
        if not existing or existing["status"] in ("complete", "failed"):
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
        return
    done = threading.Event()
    heartbeat = threading.Thread(
        target=_visibility_heartbeat, args=(sqs, queue_url, receipt, job_id, jobs, done), daemon=True,
    )
    heartbeat.start()
    try:
        item = jobs.get_item(Key={"job_id": job_id}, ConsistentRead=True)["Item"]
        end = datetime.fromisoformat(item["end_at"])
        start = datetime.fromisoformat(item["start_at"]) if item.get("start_at") else end - timedelta(days=365)
        sectors = list(item.get("sectors", []))
        dynamodb = session.client("dynamodb")
        report = run_strategy(
            prompt=item["prompt"], lookback_months=int(item["lookback_months"]),
            agent=BedrockStrategyAgent(session=session, model_id=model_id),
            universe_loader=lambda universe: list_universe_symbols(
                dynamodb, "jimmy-dev-stock-universe-membership", universe,
                sectors=sectors, gics_table_name="jimmy-dev-stock-gics-history",
            ),
            price_loader=get_adjusted_close_prices,
            market_cap_loader=get_daily_prices_and_market_caps,
            start=start, end=end, selected_sectors=sectors,
        )
        session.client("s3").put_object(
            Bucket=results_bucket, Key=f"jobs/{job_id}.json",
            Body=json.dumps({"schema_version": 4, **report}, allow_nan=False).encode(),
            ContentType="application/json",
        )
        jobs.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #status = :complete REMOVE lease_until, prompt",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":complete": "complete"},
        )
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
        logger.info("Completed strategy job %s", job_id)
    except Exception:
        logger.exception("Strategy job %s failed on attempt %s", job_id, attempt)
        jobs.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #status = :status, #error = :error REMOVE lease_until",
            ExpressionAttributeNames={"#status": "status", "#error": "error"},
            ExpressionAttributeValues={
                ":status": "failed" if attempt >= 3 else "retrying",
                ":error": "Strategy run failed; check worker logs",
            },
        )
    finally:
        done.set()
        heartbeat.join(timeout=1)


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    session = boto3.Session(region_name=os.environ["AWS_REGION"])
    sqs = session.client("sqs")
    jobs = session.resource("dynamodb").Table(os.environ["JIMMY_JOBS_TABLE"])
    queue_url = os.environ["JIMMY_JOBS_QUEUE_URL"]
    while not stopping.is_set():
        response = sqs.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=20,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        for message in response.get("Messages", []):
            process_message(
                message, session=session, jobs=jobs, sqs=sqs, queue_url=queue_url,
                results_bucket=os.environ["JIMMY_RESULTS_BUCKET"],
                model_id=os.environ["JIMMY_BEDROCK_MODEL_ID"],
            )


if __name__ == "__main__":
    main()
