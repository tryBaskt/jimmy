import json
from unittest.mock import Mock

from workers.strategy_worker import main


def test_successful_job_persists_report_before_acknowledging(monkeypatch):
    jobs = Mock()
    jobs.get_item.return_value = {"Item": {
        "prompt": "Buy momentum", "lookback_months": 3,
        "start_at": "2024-07-01T00:00:00+00:00",
        "end_at": "2025-07-01T00:00:00+00:00",
        "sectors": ["Energy"],
    }}
    sqs = Mock()
    s3 = Mock()
    dynamodb = Mock()
    session = Mock()
    session.client.side_effect = lambda service: {"dynamodb": dynamodb, "s3": s3}[service]
    monkeypatch.setattr(main, "BedrockStrategyAgent", lambda **kwargs: Mock())
    universe = Mock(return_value=["XOM"])
    monkeypatch.setattr(main, "list_universe_symbols", universe)
    run = Mock(return_value={"strategy": {"total_return": 0.1}})
    monkeypatch.setattr(main, "run_strategy", run)

    main.process_message(
        {"Body": json.dumps({"job_id": "test-job"}), "ReceiptHandle": "receipt",
         "Attributes": {"ApproximateReceiveCount": "1"}},
        session=session, jobs=jobs, sqs=sqs, queue_url="queue", results_bucket="bucket",
        model_id="model",
    )

    s3.put_object.assert_called_once()
    assert json.loads(s3.put_object.call_args.kwargs["Body"])["schema_version"] == 4
    assert run.call_args.kwargs["start"].isoformat() == "2024-07-01T00:00:00+00:00"
    assert run.call_args.kwargs["selected_sectors"] == ["Energy"]
    assert run.call_args.kwargs["universe_loader"]("SP500") == ["XOM"]
    universe.assert_called_once_with(
        dynamodb, "jimmy-dev-stock-universe-membership", "SP500",
        sectors=["Energy"], gics_table_name="jimmy-dev-stock-gics-history",
    )
    sqs.delete_message.assert_called_once_with(QueueUrl="queue", ReceiptHandle="receipt")
