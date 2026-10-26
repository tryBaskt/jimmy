from unittest.mock import Mock, patch

import pytest
from boto3.dynamodb.types import TypeSerializer

from packages.agents.strategy_agent.tools.dynamodb import (
    UniverseHistoryUnavailable, list_universe_symbols,
)


def raw(item):
    serialize = TypeSerializer().serialize
    return {key: serialize(value) for key, value in item.items()}


def client(rows, metadata=()):
    db = Mock()
    db.get_paginator.return_value.paginate.return_value = [{"Items": [raw(row) for row in rows]}]
    db.batch_get_item.return_value = {"Responses": {"metadata": [raw(item) for item in metadata]}}
    return db


def membership(identity, start="2020-01-01", **fields):
    return {"security_id": identity, "universe": "SP500", "source_symbol": "SOURCE", "added_at": start,
            "dataset_observed_from": "2020-01-01", "dataset_observed_through": "2024-01-01",
            **fields}


def test_default_uses_latest_observed_memberships_and_stable_metadata():
    db = client([membership("old", removed_at="2022-01-01"), membership("active")],
                [{"security_id": "active", "symbol": "META"}])
    assert list_universe_symbols(db, "membership", "sp500", metadata_table_name="metadata") == ["META"]
    assert db.batch_get_item.call_args.kwargs["RequestItems"]["metadata"]["Keys"] == [
        {"security_id": {"S": "active"}},
    ]


def test_historical_date_resolves_verified_ticker_and_exclusive_removal():
    identity = {"security_id": "s1", "symbol": "META", "ticker_aliases": [
        {"symbol": "FB", "effective_from": "2012-05-18", "effective_to": "2022-06-09"},
        {"symbol": "META", "effective_from": "2022-06-09"},
    ]}
    db = client([membership("s1"), membership("removed", removed_at="2022-06-09")], [identity])
    assert list_universe_symbols(db, "membership", "SP500", metadata_table_name="metadata",
                                 as_of="2022-06-09") == ["META"]
    db.get_paginator.return_value.paginate.return_value = [{"Items": [raw(membership("s1"))]}]
    assert list_universe_symbols(db, "membership", "SP500", metadata_table_name="metadata",
                                 as_of="2022-06-08") == ["FB"]


def test_observed_symbol_alias_never_becomes_effective_history():
    db = client([membership("s1")], [{"security_id": "s1", "symbol": "META",
                 "ticker_aliases": [{"symbol": "META", "observed_from": "2020-01-01"}]}])
    with pytest.raises(UniverseHistoryUnavailable, match="verified ticker"):
        list_universe_symbols(db, "membership", "SP500", metadata_table_name="metadata", as_of="2021-01-01")


@pytest.mark.parametrize("target", ["2019-12-31", "2024-01-02"])
def test_rejects_dates_outside_dataset_before_identity_lookup(target):
    db = client([membership("s1")])
    with pytest.raises(UniverseHistoryUnavailable, match="observations cover"):
        list_universe_symbols(db, "membership", "SP500", as_of=target)
    db.batch_get_item.assert_not_called()


def test_retries_unprocessed_identity_keys_and_rejects_missing_metadata():
    db = client([membership("s1")])
    pending = {"metadata": {"Keys": [{"security_id": {"S": "s1"}}]}}
    db.batch_get_item.side_effect = [{"UnprocessedKeys": pending},
        {"Responses": {"metadata": [raw({"security_id": "s1", "symbol": "AAA"})]}}]
    with patch("packages.agents.strategy_agent.tools.dynamodb.time.sleep"):
        assert list_universe_symbols(db, "membership", "SP500", metadata_table_name="metadata") == ["AAA"]
    db.batch_get_item.side_effect = None
    db.batch_get_item.return_value = {"Responses": {"metadata": []}}
    with pytest.raises(UniverseHistoryUnavailable, match="Missing stock metadata"):
        list_universe_symbols(db, "membership", "SP500", metadata_table_name="metadata")


def test_legacy_current_tables_cannot_answer_historical_dates():
    db = client([{"universe": "SP500", "symbol": "aapl"}])
    assert list_universe_symbols(db, "membership", "SP500") == ["AAPL"]
    with pytest.raises(UniverseHistoryUnavailable, match="Legacy"):
        list_universe_symbols(db, "membership", "SP500", as_of="2020-01-01")
