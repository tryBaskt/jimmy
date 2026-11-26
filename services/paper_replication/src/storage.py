"""S3 storage shared by the API and the ECS worker."""

import json
import os
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


@lru_cache
def aws_client(service):
    return boto3.client(service, config=Config(
        connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 2, "mode": "standard"},
    ))


def get(key):
    try:
        response = aws_client("s3").get_object(Bucket=os.environ["RUN_BUCKET"], Key=key)
    except ClientError as error:
        if error.response["Error"]["Code"] in {"NoSuchKey", "404"}:
            return None
        raise
    with response["Body"] as body:
        return body.read()


def get_json(key):
    data = get(key)
    return json.loads(data) if data is not None else None


def put(key, data, *, content_type="application/octet-stream", create_only=False):
    condition = {"IfNoneMatch": "*"} if create_only else {}
    try:
        aws_client("s3").put_object(
            Bucket=os.environ["RUN_BUCKET"], Key=key, Body=data, ContentType=content_type, **condition,
        )
    except ClientError as error:
        if create_only and error.response["Error"]["Code"] in {"PreconditionFailed", "412"}:
            return False
        raise
    return True


def put_json(key, value, *, create_only=False):
    return put(key, json.dumps(value).encode(), content_type="application/json", create_only=create_only)


def index_run(prefix, job):
    """Idempotent catalog entry; S3 remains the source of run state and artifacts."""
    owner_key = prefix.split("/")[1]
    aws_client("dynamodb").put_item(
        TableName=os.environ["RUN_TABLE"],
        Item={"owner": {"S": owner_key},
              "created_run": {"S": job["created_at"] + "#" + job["id"]},
              "run_id": {"S": job["id"]}},
    )


def run_page(owner_key, cursor=None, limit=20):
    arguments = dict(TableName=os.environ["RUN_TABLE"],
                     KeyConditionExpression="#owner = :owner",
                     ExpressionAttributeNames={"#owner": "owner"},
                     ExpressionAttributeValues={":owner": {"S": owner_key}},
                     ScanIndexForward=False, ConsistentRead=True, Limit=limit)
    if cursor:
        arguments["ExclusiveStartKey"] = {
            "owner": {"S": owner_key}, "created_run": {"S": cursor},
        }
    response = aws_client("dynamodb").query(**arguments)
    return ([item["run_id"]["S"] for item in response.get("Items", [])],
            response.get("LastEvaluatedKey", {}).get("created_run", {}).get("S"))
