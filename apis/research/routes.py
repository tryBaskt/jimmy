"""Research service HTTP boundary."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from paperlab.storage import Conflict, emit
from apis.contracts import NewRun
import json
import time
import uuid


def create_router(context):
    router = APIRouter()
    settings, store = context.settings, context.store
    user, project = context.user, context.project

    @router.post("/api/research/projects/{pid}/runs", status_code=202)
    def start_run(pid: str, body: NewRun, background: BackgroundTasks, identity=Depends(user)):
        project(pid, identity["sub"])
        upload, _ = store.get(f"project#{pid}", f"upload#{body.paper_id}")
        if not upload or upload["status"] != "completed" or upload["kind"] != "paper":
            raise HTTPException(422, "Select a completed paper upload")
        rid = uuid.uuid5(uuid.NAMESPACE_URL, f"{pid}:{body.idempotency_key}").hex
        row, _ = store.get(f"project#{pid}", f"run#{rid}")
        if row:
            if row["paper_id"] != body.paper_id:
                raise HTTPException(409, "Idempotency key already used for a different request")
            if row["status"] != "queued":
                return row
        else:
            row = {
                "id": rid,
                "project_id": pid,
                "owner": identity["sub"],
                "paper_id": body.paper_id,
                "status": "queued",
                "stage": "queued",
                "created_at": time.time(),
            }
            try:
                store.batch(
                    [
                        (f"project#{pid}", f"run#{rid}", row, 0),
                        (
                            f"project#{pid}",
                            f"run-index#{int(row['created_at'] * 1000000):020d}#{rid}",
                            {"run_id": rid},
                            0,
                        ),
                    ]
                )
            except Conflict:
                return store.get(f"project#{pid}", f"run#{rid}")[0]
            emit(store, pid, "run_queued", "Paper replication queued", run_id=rid)
        if settings.mode == "aws":
            import boto3

            sfn = boto3.client("stepfunctions", region_name=settings.region)
            # Stable name and input make recovery of queued start failures safe.
            sfn.start_execution(
                stateMachineArn=settings.workflow,
                name=rid,
                input=json.dumps({"project_id": pid, "run_id": rid}, sort_keys=True),
            )
        else:
            from paperlab.workflow import local_run

            background.add_task(local_run, settings, store, pid, rid)
        return row

    @router.get("/api/research/projects/{pid}/runs/{rid}")
    def get_run(pid: str, rid: str, identity=Depends(user)):
        project(pid, identity["sub"])
        row, _ = store.get(f"project#{pid}", f"run#{rid}")
        if not row:
            raise HTTPException(404)
        for name in ["plan", "prepared", "compared", "extensions", "extension_evaluated", "report"]:
            ref, _ = store.get(f"project#{pid}", f"run#{rid}#{name}")
            if ref:
                row[name] = json.loads(store.blob_get(ref["artifact"]))
        return row

    @router.post("/api/research/projects/{pid}/runs/{rid}/cancel")
    def cancel(pid: str, rid: str, identity=Depends(user)):
        project(pid, identity["sub"])
        row, rev = store.get(f"project#{pid}", f"run#{rid}")
        if not row:
            raise HTTPException(404)
        if row["status"] in {"completed", "failed", "cancelled"}:
            return row
        row.update(status="cancelled", updated_at=time.time())
        store.put(f"project#{pid}", f"run#{rid}", row, rev)
        if settings.mode == "aws":
            import boto3

            arn = settings.workflow.replace(":stateMachine:", ":execution:") + ":" + rid
            try:
                boto3.client("stepfunctions", region_name=settings.region).stop_execution(executionArn=arn)
            except Exception:
                # Persisted cancellation is authoritative; workers check it at stage and experiment boundaries.
                emit(
                    store,
                    pid,
                    "cancellation_pending",
                    "Cancellation recorded; workflow stop will be reconciled",
                    run_id=rid,
                )
        emit(store, pid, "run_cancelled", "Run cancelled", run_id=rid)
        return row

    return router
