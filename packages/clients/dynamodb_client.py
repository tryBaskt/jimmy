"""Read Jimmy stock metadata and universe membership from DynamoDB."""

from __future__ import annotations

from typing import Any

import boto3


class DynamoDBClient:
    """Small read adapter for the two existing stock tables."""

    def __init__(
        self,
        *,
        metadata_table_name: str,
        membership_table_name: str,
        region_name: str | None = None,
        dynamodb_resource: Any | None = None,
    ) -> None:
        if not metadata_table_name or not membership_table_name:
            raise ValueError("Both DynamoDB table names are required")
        resource = (
            dynamodb_resource
            if dynamodb_resource is not None
            else boto3.resource("dynamodb", region_name=region_name)
        )
        self._metadata_table = resource.Table(metadata_table_name)
        self._membership_table_name = membership_table_name
        self._query_paginator = resource.meta.client.get_paginator("query")

    def get_stock_metadata(self, symbol: str) -> dict[str, Any] | None:
        """Return current metadata for one symbol, or None if absent."""
        normalized = symbol.strip().upper()
        if not normalized:
            raise ValueError("A stock symbol is required")
        response = self._metadata_table.get_item(
            Key={"symbol": normalized}, ConsistentRead=True
        )
        return response.get("Item")

    def list_universe_symbols(self, universe: str) -> list[str]:
        """Query all current symbol memberships for a universe."""
        normalized = universe.strip().upper()
        if not normalized:
            raise ValueError("A universe is required")
        pages = self._query_paginator.paginate(
            TableName=self._membership_table_name,
            KeyConditionExpression="universe = :universe",
            ExpressionAttributeValues={":universe": normalized},
            ProjectionExpression="symbol",
            ConsistentRead=True,
        )
        return [item["symbol"] for page in pages for item in page.get("Items", [])]
