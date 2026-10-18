from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pandas as pd
import pytest
import boto3
from botocore.stub import Stubber

from packages.clients.dynamodb_client import DynamoDBClient
from packages.clients.vectorbt_client import VectorBTClient, VectorBTClientError
from packages.clients.yfinance_client import YFinanceClient, YFinanceClientError


def test_yfinance_downloads_symbols_together_and_marks_missing_prices():
    dates = pd.date_range("2025-01-01", periods=2, freq="D", tz="UTC")
    raw = pd.DataFrame(
        [[100.0, 1000], [101.0, 1100]],
        index=dates,
        columns=pd.MultiIndex.from_tuples(
            [("Close", "AAPL"), ("Volume", "AAPL")]
        ),
    )
    with patch("packages.clients.yfinance_client.yf.download", return_value=raw) as download:
        prices = YFinanceClient().get_stock_prices_over_time(
            ["aapl", "MSFT"], dates[0].to_pydatetime(),
            (dates[-1] + pd.Timedelta(days=1)).to_pydatetime(), "5Min"
        )

    assert list(prices.columns) == ["AAPL", "MSFT"]
    assert list(prices["AAPL"]) == [100.0, 101.0]
    assert prices["MSFT"].isna().all()
    assert prices.index.tz is not None
    assert download.call_count == 1
    assert download.call_args.kwargs["interval"] == "5m"
    assert download.call_args.kwargs["auto_adjust"] is True


def test_yfinance_rejects_invalid_range_before_calling_provider():
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with patch("packages.clients.yfinance_client.yf.download") as download:
        with pytest.raises(YFinanceClientError, match="precede"):
            YFinanceClient().get_stock_prices_over_time(["AAPL"], now, now, "1D")
    download.assert_not_called()


def test_vectorbt_client_returns_a_real_simulation_and_rejects_missing_prices():
    dates = pd.date_range("2025-01-01", periods=3, freq="D", tz="UTC")
    prices = pd.DataFrame({"AAPL": [100.0, 110.0, 121.0]}, index=dates)
    exposure = pd.DataFrame({"AAPL": [1.0, float("nan"), float("nan")]}, index=dates)
    result = VectorBTClient().calculate_portfolio_analytics(
        prices=prices, target_exposure=exposure, frequency="1d"
    )
    assert result.final_cumulative_return == pytest.approx(0.21)
    assert result.leverage_adjusted_direction == pytest.approx(1.0)
    assert result.prices == [100.0, 110.0, 121.0]

    prices.iloc[1, 0] = float("nan")
    with pytest.raises(VectorBTClientError, match="Prices must be finite"):
        VectorBTClient().calculate_portfolio_analytics(
            prices=prices, target_exposure=exposure, frequency="1d"
        )


def test_dynamodb_client_reads_metadata_and_all_membership_pages():
    resource = Mock()
    resource.Table.return_value.get_item.return_value = {
        "Item": {"symbol": "AAPL", "sector": "Technology"}
    }
    resource.meta.client.get_paginator.return_value.paginate.return_value = [
        {"Items": [{"symbol": "AAPL"}]},
        {"Items": [{"symbol": "MSFT"}]},
    ]
    client = DynamoDBClient(
        metadata_table_name="jimmy-dev-stock-metadata",
        membership_table_name="jimmy-dev-stock-universe-membership",
        dynamodb_resource=resource,
    )
    assert client.get_stock_metadata("aapl") == {
        "symbol": "AAPL", "sector": "Technology"
    }
    assert client.list_universe_symbols("sp500") == ["AAPL", "MSFT"]
    assert resource.Table.return_value.get_item.call_args.kwargs == {
        "Key": {"symbol": "AAPL"}, "ConsistentRead": True
    }
    assert resource.meta.client.get_paginator.return_value.paginate.call_args.kwargs[
        "ExpressionAttributeValues"
    ] == {":universe": "SP500"}


def test_dynamodb_client_uses_boto3_native_query_marshalling():
    resource = boto3.Session(
        aws_access_key_id="test", aws_secret_access_key="test", region_name="us-east-1"
    ).resource("dynamodb")
    with Stubber(resource.meta.client) as stub:
        stub.add_response(
            "query",
            {"Items": [{"symbol": {"S": "AAPL"}}]},
            {
                "TableName": "jimmy-dev-stock-universe-membership",
                "KeyConditionExpression": "universe = :universe",
                "ExpressionAttributeValues": {":universe": "SP500"},
                "ProjectionExpression": "symbol",
                "ConsistentRead": True,
            },
        )
        client = DynamoDBClient(
            metadata_table_name="jimmy-dev-stock-metadata",
            membership_table_name="jimmy-dev-stock-universe-membership",
            dynamodb_resource=resource,
        )
        assert client.list_universe_symbols("SP500") == ["AAPL"]
