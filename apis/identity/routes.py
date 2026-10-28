"""Identity service HTTP boundary."""

from fastapi import APIRouter, Depends


def create_router(context):
    router = APIRouter()
    settings, _store = context.settings, context.store
    user, _project = context.user, context.project

    @router.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @router.get("/api/identity/me")
    def me(identity=Depends(user)):
        return {"id": identity["sub"], "mode": settings.mode}

    return router
