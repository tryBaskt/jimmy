"""DynamoDB universe-membership tools."""

from __future__ import annotations

from boto3.dynamodb.types import TypeDeserializer


def list_universe_symbols(dynamodb_client, table_name: str, universe: str) -> list[str]:
    """Read all current membership rows using DynamoDB's query paginator."""
    if not table_name or not universe:
        raise ValueError("A membership table and universe are required")
    deserializer = TypeDeserializer()
    pages = dynamodb_client.get_paginator("query").paginate(
        TableName=table_name,
        KeyConditionExpression="#universe = :universe",
        ExpressionAttributeNames={"#universe": "universe", "#symbol": "symbol"},
        ExpressionAttributeValues={":universe": {"S": universe.upper()}},
        ProjectionExpression="#symbol",
        ConsistentRead=True,
    )
    return sorted({
        deserializer.deserialize(item["symbol"]).upper()
        for page in pages for item in page.get("Items", [])
    })
