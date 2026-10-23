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

from packages.agents.strategy_agent.plan import (
    BedrockStrategyAgent, StrategyPlanError, UnsupportedStrategyError,
)
from packages.agents.strategy_agent.tools.dynamodb import list_universe_symbols
from packages.agents.strategy_agent.tools.yfinance import (
    MarketDataError,
    get_adjusted_close_prices,
    get_daily_prices_and_market_caps,
)
from packages.agents.strategy_agent.workflow import run_strategy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
stopping = threading.Event()
LEASE_SECONDS = 300


def _failure_explanation(error: Exception) -> str:
    if isinstance(error, UnsupportedStrategyError):
        reason = str(error).strip()
        return (
            f"This strategy is not supported: {reason}. "
            "Try a specific price-based selection rule with monthly or quarterly rebalancing."
        )[:300]
    if isinstance(error, StrategyPlanError):
        return (
            "The strategy planner could not produce a valid test plan. State which price signal "
            "to rank, the number of stocks, weighting, and rebalance period. "
            "If you already did, this is a planner error."
        )
    if isinstance(error, MarketDataError):
        return "Historical stock prices could not be loaded. This is a data-source issue; please try later."
    if isinstance(error, ValueError):
        detail = str(error)
        if detail.startswith("Only ") and "stocks" in detail:
            return f"{detail[:180]}. Try fewer stocks or a shorter lookback."
        if detail.startswith("No valid rebalance dates"):
            return "No rebalance dates had enough price history. Try a shorter lookback."
        if detail.startswith(("The agent", "A portfolio", "Each position", "Model-suggested")):
            return "The strategy model produced an invalid portfolio decision. This is a model error, not necessarily a prompt issue."
        return "The strategy could not be backtested with the available data. Review the selection rule and lookback."
    return "The strategy run failed because of a service error. This is not necessarily a prompt issue; please try later."


def _stop(_number, _frame) -> None:
    stopping.set()


def _visibility_heartbeat(sqs, queue_url: str, receipt: str, job_id: str, jobs,
                          done: threading.Event) -> None:
    while not done.wait(60) and not stopping.is_set():
        try:
            sqs.change_message_visibility(
                QueueUrl=queue_url, ReceiptHandle=receipt, VisibilityTimeout=LEASE_SECONDS,
            )
            jobs.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET lease_until = :lease",
                ConditionExpression="#status = :running",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":lease": int(time.time()) + LEASE_SECONDS, ":running": "running"},
            )
            logger.info("Renewed strategy job %s lease", job_id)
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
            ConditionExpression="#status = :queued",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":running": "running", ":queued": "queued",
                ":lease": now + LEASE_SECONDS, ":attempt": attempt,
            },
        )
    except jobs.meta.client.exceptions.ConditionalCheckFailedException:
        existing = jobs.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
        if existing and existing["status"] == "retrying":
            jobs.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #status = :failed, #error = :error REMOVE lease_until, prompt",
                ConditionExpression="#status = :retrying",
                ExpressionAttributeNames={"#status": "status", "#error": "error"},
                ExpressionAttributeValues={
                    ":failed": "failed", ":retrying": "retrying",
                    ":error": "This earlier run failed. Please submit a new strategy test.",
                },
            )
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
            logger.info("Closed legacy retrying strategy job %s", job_id)
        elif not existing or existing["status"] in ("complete", "failed"):
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
            logger.info("Discarded completed strategy job message %s", job_id)
        return
    logger.info("Started strategy job %s attempt=%s", job_id, attempt)
    done = threading.Event()
    heartbeat = threading.Thread(
        target=_visibility_heartbeat, args=(sqs, queue_url, receipt, job_id, jobs, done), daemon=True,
    )
    heartbeat.start()
    try:
        item = jobs.get_item(Key={"job_id": job_id}, ConsistentRead=True)["Item"]
        end = datetime.fromisoformat(item["end_at"])
        dynamodb = session.client("dynamodb")
        report = run_strategy(
            prompt=item["prompt"], lookback_months=int(item["lookback_months"]),
            agent=BedrockStrategyAgent(session=session, model_id=model_id),
            universe_loader=lambda universe: list_universe_symbols(
                dynamodb, "jimmy-dev-stock-universe-membership", universe,
            ),
            price_loader=get_adjusted_close_prices,
            market_cap_loader=get_daily_prices_and_market_caps,
            start=end - timedelta(days=365), end=end, job_id=job_id,
        )
        logger.info("Strategy job %s report ready; uploading result", job_id)
        session.client("s3").put_object(
            Bucket=results_bucket, Key=f"jobs/{job_id}.json",
            Body=json.dumps({"schema_version": 3, **report}, allow_nan=False).encode(),
            ContentType="application/json",
        )
        logger.info("Strategy job %s result uploaded; recording completion", job_id)
        jobs.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #status = :complete REMOVE lease_until, prompt",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":complete": "complete"},
        )
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
        logger.info("Completed strategy job %s", job_id)
    except Exception as error:
        logger.exception("Strategy job %s failed", job_id)
        explanation = _failure_explanation(error)
        jobs.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #status = :failed, #error = :error REMOVE lease_until, prompt",
            ExpressionAttributeNames={"#status": "status", "#error": "error"},
            ExpressionAttributeValues={
                ":failed": "failed", ":error": explanation,
            },
        )
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
        logger.info("Strategy job %s status=failed reason=%s", job_id, explanation)
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
    logger.info("Strategy worker started; polling for jobs")
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
