"""Persist terminal workflow failures even when an ECS task dies before updating state."""

import json
import time
from paperlab.config import Settings
from paperlab.storage import Conflict, emit, make_store


def handler(event, context):
    settings = Settings.load()
    store = make_store(settings)
    detail = event.get("detail", {})
    if detail.get("status") not in {"FAILED", "TIMED_OUT", "ABORTED"}:
        return {"ignored": True}
    import boto3

    execution = boto3.client("stepfunctions", region_name=settings.region).describe_execution(
        executionArn=detail["executionArn"]
    )
    identity = json.loads(execution["input"])
    pid, rid = identity["project_id"], identity["run_id"]
    for _ in range(3):
        row, rev = store.get(f"project#{pid}", f"run#{rid}")
        if not row or row["status"] in {"completed", "cancelled", "failed"}:
            return {"unchanged": True}
        row.update(
            status="failed",
            error=f"Workflow terminated ({detail['status']}); inspect stage activity.",
            updated_at=time.time(),
        )
        try:
            store.put(f"project#{pid}", f"run#{rid}", row, rev)
            emit(store, pid, "workflow_failed", row["error"], run_id=rid)
            return {"reconciled": True}
        except Conflict:
            continue
    raise Conflict("Could not reconcile terminal execution")
