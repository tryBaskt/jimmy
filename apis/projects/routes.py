"""Projects service HTTP boundary."""

from fastapi import APIRouter, Depends
from paperlab.storage import emit
from apis.contracts import NewProject
import time
import uuid


def create_router(context):
    router = APIRouter()
    _settings, store = context.settings, context.store
    user, project = context.user, context.project

    @router.get("/api/projects")
    def projects(after: str = "", identity=Depends(user)):
        rows = store.query(f"owner#{identity['sub']}", "project#", after, 100)
        return {"items": [v for _, v in rows], "cursor": rows[-1][0] if rows else after}

    @router.post("/api/projects", status_code=201)
    def create(body: NewProject, identity=Depends(user)):
        pid = uuid.uuid4().hex
        item = {"id": pid, "owner": identity["sub"], "title": body.title, "created_at": time.time()}
        store.batch(
            [(f"project#{pid}", "meta", item, 0), (f"owner#{identity['sub']}", f"project#{pid}", item, 0)]
        )
        emit(store, pid, "project_created", "Project workspace created")
        return item

    @router.get("/api/projects/{pid}")
    def get_project(pid: str, identity=Depends(user)):
        item = project(pid, identity["sub"])
        uploads = [v for _, v in store.query(f"project#{pid}", "upload#", limit=100)]
        runs = [
            store.get(f"project#{pid}", f"run#{ref['run_id']}")[0]
            for _, ref in store.query(f"project#{pid}", "run-index#", limit=100)
        ]
        runs = [row for row in runs if row is not None]
        return {**item, "uploads": uploads, "runs": runs}

    return router
