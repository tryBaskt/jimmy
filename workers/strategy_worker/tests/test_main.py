import json
from unittest.mock import Mock

from workers.strategy_worker import main
from packages.agents.strategy_agent.plan import StrategyPlanError


def test_successful_job_persists_report_before_acknowledging(monkeypatch):
    jobs = Mock()
    jobs.get_item.return_value = {"Item": {
        "prompt": "Buy momentum", "lookback_months": 3,
        "end_at": "2025-07-01T00:00:00+00:00",
    }}
    sqs = Mock()
    s3 = Mock()
    dynamodb = Mock()
    session = Mock()
    session.client.side_effect = lambda service: {"dynamodb": dynamodb, "s3": s3}[service]
    monkeypatch.setattr(main, "BedrockStrategyAgent", lambda **kwargs: Mock())
    monkeypatch.setattr(main, "run_strategy", lambda **kwargs: {"strategy": {"total_return": 0.1}})

    main.process_message(
        {"Body": json.dumps({"job_id": "test-job"}), "ReceiptHandle": "receipt",
         "Attributes": {"ApproximateReceiveCount": "1"}},
        session=session, jobs=jobs, sqs=sqs, queue_url="queue", results_bucket="bucket",
        model_id="model",
    )

    s3.put_object.assert_called_once()
    assert json.loads(s3.put_object.call_args.kwargs["Body"])["schema_version"] == 3
    sqs.delete_message.assert_called_once_with(QueueUrl="queue", ReceiptHandle="receipt")


def test_invalid_plan_fails_job_without_waiting_for_queue_retry(monkeypatch):
    jobs = Mock()
    jobs.get_item.return_value = {"Item": {
        "prompt": "Buy momentum", "lookback_months": 3,
        "end_at": "2025-07-01T00:00:00+00:00",
    }}
    sqs = Mock()
    session = Mock()
    monkeypatch.setattr(main, "BedrockStrategyAgent", lambda **kwargs: Mock())

    def invalid_plan(**kwargs):
        raise StrategyPlanError("Invalid ranking step")

    monkeypatch.setattr(main, "run_strategy", invalid_plan)
    main.process_message(
        {"Body": json.dumps({"job_id": "test-job"}), "ReceiptHandle": "receipt",
         "Attributes": {"ApproximateReceiveCount": "1"}},
        session=session, jobs=jobs, sqs=sqs, queue_url="queue", results_bucket="bucket",
        model_id="model",
    )

    failed = jobs.update_item.call_args.kwargs
    assert failed["ExpressionAttributeValues"][":failed"] == "failed"
    assert failed["ExpressionAttributeValues"][":error"] == "Invalid ranking step"
    sqs.delete_message.assert_called_once_with(QueueUrl="queue", ReceiptHandle="receipt")
