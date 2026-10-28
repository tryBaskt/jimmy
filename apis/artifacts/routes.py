"""Artifacts service HTTP boundary."""

from fastapi import APIRouter, Depends, HTTPException, Response


def create_router(context):
    router = APIRouter()
    settings, store = context.settings, context.store
    user, project = context.user, context.project

    @router.get("/api/artifacts/projects/{pid}/artifacts")
    def artifacts(pid: str, identity=Depends(user)):
        project(pid, identity["sub"])
        return {
            "items": [{"id": k[9:], **v} for k, v in store.query(f"project#{pid}", "artifact#", limit=200)]
        }

    @router.get("/api/artifacts/projects/{pid}/artifacts/{aid}")
    def get_artifact(pid: str, aid: str, identity=Depends(user)):
        project(pid, identity["sub"])
        ref, _ = store.get(f"project#{pid}", f"artifact#{aid}")
        if not ref:
            raise HTTPException(404)
        if settings.mode == "aws":
            return {
                "url": store.s3.generate_presigned_url(
                    "get_object", Params={"Bucket": settings.bucket, "Key": ref["key"]}, ExpiresIn=120
                )
            }
        return Response(store.blob_get(ref["key"]), media_type="application/json")

    return router
