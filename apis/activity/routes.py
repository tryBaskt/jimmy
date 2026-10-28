"""Activity service HTTP boundary."""

from fastapi import APIRouter, Depends, HTTPException
from paperlab.storage import digest
import secrets
import time


def create_router(context):
    router = APIRouter()
    settings, store = context.settings, context.store
    user, project = context.user, context.project

    @router.get("/api/activity/projects/{pid}/activity")
    def activity(pid: str, after: str = "", identity=Depends(user)):
        project(pid, identity["sub"])
        if after and not (after.startswith("event#") and after[6:].isdigit()):
            raise HTTPException(422, "Invalid activity cursor")
        rows = store.query(f"project#{pid}", "event#", after, 100)
        return {"items": [v for _, v in rows], "cursor": rows[-1][0] if rows else after}

    @router.post("/api/activity/realtime-ticket")
    def ticket(identity=Depends(user)):
        if settings.mode != "aws":
            return {"transport": "polling"}
        token = secrets.token_urlsafe(32)
        store.put(
            "tickets",
            digest(token.encode()),
            {"owner": identity["sub"], "until": time.time() + 60, "session_until": identity["exp"]},
            0,
        )
        return {"ticket": token}

    return router
