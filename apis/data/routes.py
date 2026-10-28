"""Data service HTTP boundary."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from paperlab.storage import digest, emit
from apis.contracts import NewUpload
import time
import uuid
from hashlib import sha256


def create_router(context):
    router = APIRouter()
    settings, store = context.settings, context.store
    user, project = context.user, context.project

    @router.post("/api/data/projects/{pid}/uploads", status_code=201)
    def new_upload(pid: str, body: NewUpload, identity=Depends(user)):
        project(pid, identity["sub"])
        ext = ".pdf" if body.kind == "paper" else ".csv"
        if not body.name.lower().endswith(ext):
            raise HTTPException(422, f"Expected {ext} file")
        uid = uuid.uuid4().hex
        key = f"staging/{pid}/{uid}"
        row = {
            **body.model_dump(),
            "id": uid,
            "key": key,
            "status": "pending",
            "expires_at": time.time() + 900,
        }
        store.put(f"project#{pid}", f"upload#{uid}", row, 0)
        if settings.mode == "aws":
            form = store.s3.generate_presigned_post(
                Bucket=settings.bucket,
                Key=key,
                Conditions=[["content-length-range", body.size, body.size]],
                ExpiresIn=900,
            )
            return {"id": uid, "form": form}
        return {"id": uid, "url": f"/api/data/projects/{pid}/uploads/{uid}/content"}

    @router.put("/api/data/projects/{pid}/uploads/{uid}/content", status_code=204)
    async def local_upload(pid: str, uid: str, request: Request, identity=Depends(user)):
        project(pid, identity["sub"])
        if settings.mode != "local":
            raise HTTPException(404)
        row, _ = store.get(f"project#{pid}", f"upload#{uid}")
        if not row or row["status"] != "pending" or row["expires_at"] < time.time():
            raise HTTPException(409, "Upload expired or already completed")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > row["size"]:
                raise HTTPException(413, "Upload exceeds declared size")
        if len(content) != row["size"] or sha256(content).hexdigest() != row["checksum"]:
            raise HTTPException(422, "Upload size or checksum mismatch")
        store.blob_put(row["key"], bytes(content), "application/octet-stream")
        return Response(status_code=204)

    @router.post("/api/data/projects/{pid}/uploads/{uid}/complete")
    def complete(pid: str, uid: str, identity=Depends(user)):
        project(pid, identity["sub"])
        row, rev = store.get(f"project#{pid}", f"upload#{uid}")
        if not row:
            raise HTTPException(404)
        if row["status"] == "completed":
            return row
        if row["expires_at"] < time.time():
            raise HTTPException(409, "Upload expired")
        try:
            content = store.blob_get(row["key"])
        except (FileNotFoundError, OSError):
            raise HTTPException(409, "Upload not received") from None
        if len(content) != row["size"] or digest(content) != row["checksum"]:
            raise HTTPException(422, "Upload checksum/size mismatch")
        if row["kind"] == "paper" and not content.startswith(b"%PDF-"):
            raise HTTPException(422, "Invalid PDF signature")
        key = f"projects/{pid}/inputs/{uid}/{row['checksum']}"
        store.blob_put(key, content, "application/pdf" if row["kind"] == "paper" else "text/csv")
        row.update(key=key, status="completed")
        store.put(f"project#{pid}", f"upload#{uid}", row, rev)
        emit(
            store, pid, "upload_completed", f"Uploaded {row['name']}", detail={"id": uid, "kind": row["kind"]}
        )
        return row

    return router
