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
