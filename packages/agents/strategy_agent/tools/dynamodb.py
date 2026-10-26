"""Read observed universe history and resolve stable security identities."""

from __future__ import annotations

import time
from datetime import date, datetime

from boto3.dynamodb.types import TypeDeserializer


class UniverseHistoryUnavailable(ValueError):
    """Requested membership or ticker identity is outside verified coverage."""


def _date(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(value[:10]).isoformat()


def _metadata(dynamodb_client, table_name: str, security_ids: set[str]) -> dict:
    deserialize = TypeDeserializer().deserialize
    result = {}
    ordered = sorted(security_ids)
    for offset in range(0, len(ordered), 100):
        pending = {table_name: {
            "Keys": [{"security_id": {"S": key}} for key in ordered[offset:offset + 100]],
            "ConsistentRead": True,
        }}
        for attempt in range(6):
            response = dynamodb_client.batch_get_item(RequestItems=pending)
            for raw in response.get("Responses", {}).get(table_name, []):
                item = {key: deserialize(value) for key, value in raw.items()}
                result[item["security_id"]] = item
            pending = response.get("UnprocessedKeys", {})
            if not pending:
                break
            if attempt < 5:
                time.sleep(min(0.05 * 2 ** attempt, 1))
        if pending:
            raise UniverseHistoryUnavailable("Stock identity reads remain unprocessed; retry later")
    missing = security_ids - result.keys()
    if missing:
        raise UniverseHistoryUnavailable(f"Missing stock metadata for {len(missing)} securities")
    return result


def list_universe_symbols(
    dynamodb_client, table_name: str, universe: str, *,
    metadata_table_name: str = "jimmy-dev-stock-metadata",
    as_of: str | date | datetime | None = None,
) -> list[str]:
    """Resolve the latest observed membership, or a dated observation.

    CSV intervals describe snapshot observations, not official index effective
    dates. An explicit historical date also requires verified ticker aliases.
    Legacy symbol-only tables remain readable for current membership only.
    """
    if not table_name or not universe:
        raise ValueError("A membership table and universe are required")
    deserialize = TypeDeserializer().deserialize
    pages = dynamodb_client.get_paginator("query").paginate(
        TableName=table_name,
        KeyConditionExpression="#universe = :universe",
        ExpressionAttributeNames={"#universe": "universe"},
        ExpressionAttributeValues={":universe": {"S": universe.upper()}},
        ConsistentRead=True,
    )
    rows = [
        {key: deserialize(value) for key, value in raw.items()}
        for page in pages for raw in page.get("Items", [])
    ]
    if not rows:
        raise UniverseHistoryUnavailable(f"No membership coverage for {universe.upper()}")
    historical = [row for row in rows if "security_id" in row]
    if not historical:
        if as_of is not None:
            raise UniverseHistoryUnavailable("Legacy memberships have no historical coverage")
        return sorted({row["symbol"].upper() for row in rows})
    if len(historical) != len(rows):
        raise UniverseHistoryUnavailable("Mixed legacy and historical membership schemas")
    bounds = {
        (row.get("dataset_observed_from"), row.get("dataset_observed_through"))
        for row in rows
    }
    if len(bounds) != 1 or None in next(iter(bounds)):
        raise UniverseHistoryUnavailable("Membership dataset coverage is missing or inconsistent")
    first, last = next(iter(bounds))
    target = _date(as_of) if as_of is not None else last
    if not first <= target <= last:
        raise UniverseHistoryUnavailable(f"Membership observations cover {first} through {last}")
    selected = [row for row in rows
                if row["added_at"] <= target
                and (not row.get("removed_at") or target < row["removed_at"])]
    identities = _metadata(dynamodb_client, metadata_table_name,
                           {row["security_id"] for row in selected})
    symbols = set()
    for row in selected:
        item = identities[row["security_id"]]
        if as_of is None:
            symbol = item.get("symbol")
        else:
            aliases = [alias for alias in item.get("ticker_aliases", [])
                       if alias.get("effective_from")
                       and alias["effective_from"] <= target
                       and (not alias.get("effective_to") or target < alias["effective_to"])]
            symbol = aliases[0].get("symbol") if len(aliases) == 1 else None
        if not symbol:
            raise UniverseHistoryUnavailable(
                f"No unambiguous verified ticker for {row['security_id']} at {target}"
            )
        symbols.add(symbol.upper())
    return sorted(symbols)
