"""Idempotently index existing S3 requests without reading paper contents or model secrets."""
import json
import re
import sys

import boto3

bucket, table = sys.argv[1:]
s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")
count = 0
for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="runs/"):
    for item in page.get("Contents", []):
        match = re.fullmatch(r"runs/([a-f0-9]{64})/([a-f0-9-]{36})/request\.json", item["Key"])
        if not match:
            continue
        response = s3.get_object(Bucket=bucket, Key=item["Key"])
        with response["Body"] as body:
            job = json.load(body)
        if job["id"] != match[2]:
            raise ValueError("Run request does not match its S3 path")
        ddb.put_item(TableName=table, Item={
            "owner": {"S": match[1]},
            "created_run": {"S": job["created_at"] + "#" + job["id"]},
            "run_id": {"S": job["id"]},
        })
        count += 1
print(f"Indexed {count} existing run requests")
