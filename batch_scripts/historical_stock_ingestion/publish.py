"""Back up, publish, and verify the authorized Jimmy dev history migration.

Table replacement is performed by a reviewed Terraform plan, never by this tool.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import time

ACCOUNT = "499133675835"
TABLES = {
    "stock_metadata": ("jimmy-dev-stock-metadata", ("security_id",)),
    "stock_universe_membership": (
        "jimmy-dev-stock-universe-membership", ("universe", "membership_key")
    ),
    "stock_gics_history": (
        "jimmy-dev-stock-gics-history", ("security_id", "classification_key")
    ),
}


def encode(value):
    if isinstance(value, Decimal):
        return {"__decimal__": str(value)}
    raise TypeError(f"Unsupported backup value: {type(value).__name__}")


def decode(value):
    if set(value) == {"__decimal__"}:
        return Decimal(value["__decimal__"])
    return value


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, default=encode, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def connect(profile):
    import boto3
    from botocore.config import Config
    config = Config(connect_timeout=10, read_timeout=30,
                    retries={"mode": "standard", "total_max_attempts": 4})
    session = boto3.Session(profile_name=profile, region_name="us-east-1")
    if session.client("sts", config=config).get_caller_identity()["Account"] != ACCOUNT:
        raise ValueError("Refusing migration outside the Jimmy dev AWS account")
    return session.resource("dynamodb", config=config)


def read_items(db, name):
    return [item for page in db.meta.client.get_paginator("scan").paginate(
        TableName=name, ConsistentRead=True
    ) for item in page.get("Items", [])]


def backup(db, output):
    result = {"account": ACCOUNT, "region": "us-east-1", "created_at":
              datetime.now(timezone.utc).isoformat(), "tables": {}}
    for _, (name, _) in TABLES.items():
        try:
            description = db.meta.client.describe_table(TableName=name)["Table"]
        except db.meta.client.exceptions.ResourceNotFoundException:
            continue
        result["tables"][name] = {
            "key_schema": description["KeySchema"],
            "attribute_definitions": description["AttributeDefinitions"],
            "items": read_items(db, name),
        }
    if output.exists():
        raise ValueError("Backup already exists; choose a new path to preserve it")
    write_json(output, result)
    print(json.dumps({name: len(t["items"]) for name, t in result["tables"].items()}))


def validate_plan(plan):
    if not isinstance(plan, dict):
        raise ValueError("Plan must be a JSON object")
    identities = set()
    for logical, (_, keys) in TABLES.items():
        records = plan.get(logical)
        if not isinstance(records, list):
            raise ValueError(f"Missing record list: {logical}")
        seen = set()
        for item in records:
            if not isinstance(item, dict):
                raise ValueError(f"Invalid item in {logical}")
            key = tuple(item.get(k) for k in keys)
            if any(not isinstance(v, str) or not v for v in key) or key in seen:
                raise ValueError(f"Invalid or duplicate key in {logical}")
            seen.add(key)
            if logical == "stock_metadata":
                identities.add(item["security_id"])
            elif item.get("security_id") not in identities:
                raise ValueError(f"Unknown security identity in {logical}")
            if logical == "stock_gics_history":
                if item.get("taxonomy") != "GICS":
                    raise ValueError("Only explicit GICS records belong in GICS history")
                if not item.get("effective_from") and not item.get("observed_at"):
                    raise ValueError("Classification requires an effective or observed date")
    if not identities or not plan["stock_universe_membership"]:
        raise ValueError("Refusing an empty metadata or membership migration")


def check_schemas(db):
    for name, keys in TABLES.values():
        table = db.meta.client.describe_table(TableName=name)["Table"]
        expected = [{"AttributeName": key, "KeyType": "HASH" if i == 0 else "RANGE"}
                    for i, key in enumerate(keys)]
        if table["KeySchema"] != expected or table["TableStatus"] != "ACTIVE":
            raise ValueError(f"Table {name} is not ACTIVE with the expected history schema")


def publish(db, plan):
    validate_plan(plan)
    check_schemas(db)
    # Upsert only; repeated runs resume safely without deleting partial progress.
    for logical, (name, _) in TABLES.items():
        records = plan[logical]
        for offset in range(0, len(records), 25):
            pending = [{"PutRequest": {"Item": item}}
                       for item in records[offset:offset + 25]]
            for attempt in range(8):
                response = db.meta.client.batch_write_item(RequestItems={name: pending})
                pending = response.get("UnprocessedItems", {}).get(name, [])
                if not pending:
                    break
                if attempt == 7:
                    raise RuntimeError(f"Unprocessed writes remain on {name}; rerun to resume")
                time.sleep(min(2 ** attempt, 20))
        print(f"Published {len(records)} records to {name}", flush=True)


def verify(db, plan):
    validate_plan(plan)
    check_schemas(db)
    counts = {}
    for logical, (name, keys) in TABLES.items():
        actual = read_items(db, name)
        expected = {tuple(item[k] for k in keys): item for item in plan[logical]}
        found = {tuple(item[k] for k in keys): item for item in actual}
        if expected != found:
            raise ValueError(f"Read-back mismatch in {name}; expected {len(expected)}, got {len(found)}")
        counts[name] = len(actual)
    print(json.dumps({"verified": counts, "plan_sha256": hashlib.sha256(
        json.dumps(plan, sort_keys=True, default=encode).encode()).hexdigest()}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["backup", "publish", "verify", "validate"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--profile", default="siby_baskt")
    args = parser.parse_args()
    if args.operation == "backup":
        backup(connect(args.profile), args.path)
    else:
        plan = json.loads(args.path.read_text(), object_hook=decode, parse_float=Decimal)
        validate_plan(plan)
        if args.operation != "validate":
            globals()[args.operation](connect(args.profile), plan)
        else:
            print("Plan structure and references validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
