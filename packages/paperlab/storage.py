"""SQLite development and DynamoDB/S3 production stores with the same contracts."""

from __future__ import annotations
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import threading
import time
import uuid


class Conflict(Exception):
    pass


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def digest(value):
    return sha256(value if isinstance(value, bytes) else encode(value).encode()).hexdigest()


class LocalStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS records (pk TEXT, sk TEXT, rev INTEGER, body TEXT, PRIMARY KEY(pk, sk))"
            )

    @contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.root / "state.sqlite", timeout=30)
            try:
                yield db
                db.commit()
            finally:
                db.close()

    def get(self, pk, sk):
        with self.connection() as db:
            row = db.execute("SELECT body,rev FROM records WHERE pk=? AND sk=?", (pk, sk)).fetchone()
        return (json.loads(row[0]), row[1]) if row else (None, 0)

    def batch(self, writes):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for pk, sk, value, expected in writes:
                row = db.execute("SELECT rev FROM records WHERE pk=? AND sk=?", (pk, sk)).fetchone()
                rev = row[0] if row else 0
                if expected is not None and rev != expected:
                    db.rollback()
                    raise Conflict()
                db.execute(
                    "INSERT OR REPLACE INTO records VALUES (?,?,?,?)", (pk, sk, rev + 1, encode(value))
                )

    def put(self, pk, sk, value, expected=None):
        self.batch([(pk, sk, value, expected)])

    def query(self, pk, prefix="", after="", limit=100):
        with self.connection() as db:
            rows = db.execute(
                "SELECT sk,body FROM records WHERE pk=? AND sk LIKE ? AND sk>? ORDER BY sk LIMIT ?",
                (pk, prefix + "%", after, limit),
            ).fetchall()
        return [(sk, json.loads(body)) for sk, body in rows]

    def blob_put(self, key, body, content_type="application/json"):
        path = self.root / "objects" / key
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as f:
                f.write(body)
        except FileExistsError:
            if path.read_bytes() != body:
                raise Conflict("Immutable artifact already exists with other contents")
        return key

    def blob_get(self, key):
        path = (self.root / "objects" / key).resolve()
        if not path.is_relative_to(self.root / "objects"):
            raise ValueError("Invalid artifact path")
        return path.read_bytes()


class AWSStore:
    def __init__(self, settings):
        import boto3
        from botocore.config import Config

        self.settings = settings
        session = boto3.Session(region_name=settings.region)
        config = Config(
            retries={"total_max_attempts": 4, "mode": "adaptive"}, connect_timeout=5, read_timeout=60
        )
        self.table = session.resource("dynamodb", config=config).Table(settings.table)
        self.client = self.table.meta.client
        self.s3 = session.client("s3", config=config)

    def get(self, pk, sk):
        row = self.table.get_item(Key={"pk": pk, "sk": sk}, ConsistentRead=True).get("Item")
        return (json.loads(row["body"]), int(row["rev"])) if row else (None, 0)

    def batch(self, writes):
        transactions = []
        for pk, sk, value, expected in writes:
            if expected is None:
                _, expected = self.get(pk, sk)
            item = {
                "TableName": self.settings.table,
                "Item": {"pk": pk, "sk": sk, "rev": expected + 1, "body": encode(value)},
                "ConditionExpression": "attribute_not_exists(pk)" if expected == 0 else "rev = :expected",
            }
            if pk == "tickets" or pk == "connections" or pk.startswith("subscriptions#"):
                item["Item"]["ttl"] = int(value.get("until", time.time()))
            if expected:
                item["ExpressionAttributeValues"] = {":expected": expected}
            transactions.append({"Put": item})
        try:
            self.client.transact_write_items(TransactItems=transactions)
        except self.client.exceptions.TransactionCanceledException as exc:
            if any(
                x.get("Code") == "ConditionalCheckFailed" for x in exc.response.get("CancellationReasons", [])
            ):
                raise Conflict() from exc
            raise

    def put(self, pk, sk, value, expected=None):
        self.batch([(pk, sk, value, expected)])

    def query(self, pk, prefix="", after="", limit=100):
        from boto3.dynamodb.conditions import Key

        condition = Key("pk").eq(pk)
        if prefix:
            condition &= Key("sk").begins_with(prefix)
        params = {"KeyConditionExpression": condition, "Limit": limit, "ConsistentRead": True}
        if after:
            params["ExclusiveStartKey"] = {"pk": pk, "sk": after}
        response = self.table.query(**params)
        return [(x["sk"], json.loads(x["body"])) for x in response["Items"]]

    def blob_put(self, key, body, content_type="application/json"):
        try:
            self.s3.put_object(
                Bucket=self.settings.bucket, Key=key, Body=body, ContentType=content_type, IfNoneMatch="*"
            )
        except self.s3.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] not in {"PreconditionFailed", "412"}:
                raise
            if digest(self.blob_get(key)) != digest(body):
                raise Conflict("Immutable object mismatch") from exc
        return key

    def blob_get(self, key):
        with self.s3.get_object(Bucket=self.settings.bucket, Key=key)["Body"] as body:
            return body.read()


def make_store(settings):
    return AWSStore(settings) if settings.mode == "aws" else LocalStore(settings.root)


def emit(store, project, kind, summary, *, run_id=None, experiment_id=None, detail=None, provisional=False):
    pk = f"project#{project}"
    if detail is not None and len(encode(detail).encode()) > 32000:
        detail = {
            "artifact": artifact(store, project, run_id or "activity", "activity-detail", detail),
            "message": "Large event detail is available as an artifact",
        }
    for _ in range(12):
        counter, rev = store.get(pk, "cursor")
        seq = (counter or {}).get("sequence", 0) + 1
        cursor = f"event#{seq:016d}"
        event = {
            "id": uuid.uuid4().hex,
            "sequence": seq,
            "cursor": cursor,
            "project_id": project,
            "run_id": run_id,
            "experiment_id": experiment_id,
            "kind": kind,
            "summary": summary,
            "detail": detail,
            "provisional": provisional,
            "timestamp": time.time(),
        }
        try:
            store.batch([(pk, "cursor", {"sequence": seq}, rev), (pk, cursor, event, 0)])
            return event
        except Conflict:
            continue
    raise Conflict("Activity stream contention")


def artifact(store, project, run, kind, value):
    body = encode(value).encode()
    key = f"projects/{project}/runs/{run}/{kind}/{digest(body)}.json"
    store.blob_put(key, body)
    store.put(f"project#{project}", f"artifact#{digest(key)}", {"key": key, "kind": kind, "run_id": run})
    return key
