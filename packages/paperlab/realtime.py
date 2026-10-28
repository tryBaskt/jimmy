"""Single-use WebSocket tickets; project-authorized notifications of persisted events."""

import json
import time
from boto3.dynamodb.types import TypeDeserializer
from paperlab.config import Settings
from paperlab.storage import Conflict, digest, make_store


def connection_handler(event, context):
    settings = Settings.load()
    store = make_store(settings)
    route = event["requestContext"]["routeKey"]
    connection = event["requestContext"]["connectionId"]
    if route == "$connect":
        token = (event.get("queryStringParameters") or {}).get("ticket", "")
        row, rev = store.get("tickets", digest(token.encode()))
        if not row or row["until"] < time.time() or row.get("used"):
            return {"statusCode": 401}
        row["used"] = True
        try:
            store.batch(
                [
                    ("tickets", digest(token.encode()), row, rev),
                    ("connections", connection, {"owner": row["owner"], "until": row["session_until"]}, 0),
                ]
            )
        except Conflict:
            return {"statusCode": 401}
    elif route == "subscribe":
        row, _ = store.get("connections", connection)
        payload = json.loads(event.get("body") or "{}")
        pid = payload.get("project_id", "")
        project, _ = store.get(f"project#{pid}", "meta")
        if not row or row["until"] <= time.time() or not project or project["owner"] != row["owner"]:
            return {"statusCode": 403}
        store.put(f"subscriptions#{pid}", connection, row)
    elif route == "$disconnect":
        row, rev = store.get("connections", connection)
        if row:
            row["until"] = 0
            store.put("connections", connection, row, rev)
    return {"statusCode": 200}


def publisher(event, context):
    import boto3

    settings = Settings.load()
    store = make_store(settings)
    client = boto3.client(
        "apigatewaymanagementapi", endpoint_url=settings.websocket_endpoint, region_name=settings.region
    )
    decoder = TypeDeserializer()
    for record in event.get("Records", []):
        if record.get("eventName") != "INSERT":
            continue
        image = {k: decoder.deserialize(v) for k, v in record["dynamodb"]["NewImage"].items()}
        if not image.get("sk", "").startswith("event#"):
            continue
        activity = json.loads(image["body"])
        for connection, subscription in store.query(f"subscriptions#{activity['project_id']}", limit=100):
            active, _ = store.get("connections", connection)
            project, _ = store.get(f"project#{activity['project_id']}", "meta")
            if (
                not active
                or active["until"] <= time.time()
                or not project
                or active["owner"] != project["owner"]
            ):
                continue
            try:
                client.post_to_connection(
                    ConnectionId=connection,
                    Data=json.dumps(
                        {
                            "project_id": activity["project_id"],
                            "cursor": activity["cursor"],
                            "kind": activity["kind"],
                        }
                    ).encode(),
                )
            except client.exceptions.GoneException:
                row, rev = store.get("connections", connection)
                if row:
                    row["until"] = 0
                    store.put("connections", connection, row, rev)
    return {"ok": True}
