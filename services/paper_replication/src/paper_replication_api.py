import base64
import io
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from mangum import Mangum
from pypdf import PdfReader
from starlette.concurrency import run_in_threadpool

from security.workflow_auth import current_user
from clients.agentcore_client import invoke
from http_utils.app import create_app
from models import MAX_DATA_ATTEMPTS

router = APIRouter()


@router.post("/runs", status_code=202)
async def start(paper: UploadFile = File(...), extrapolations: int = Form(3, ge=0, le=5),
                data_attempts: int = Form(MAX_DATA_ATTEMPTS, ge=1, le=MAX_DATA_ATTEMPTS),
                workers: int = Form(4, ge=1, le=8),
                symbols: str = Form("", max_length=1000), run_id: UUID | None = Form(None),
                owner: str = Depends(current_user)):
    try:
        contents = await paper.read(3 * 1024 * 1024 + 1)
        if len(contents) > 3 * 1024 * 1024:
            raise HTTPException(413, "Paper must be no larger than 3 MB")
        if not (paper.filename or "").lower().endswith(".pdf") or not contents.startswith(b"%PDF-"):
            raise HTTPException(400, "Upload a PDF paper")
        def validate():
            try:
                reader = PdfReader(io.BytesIO(contents))
                if reader.is_encrypted or not 1 <= len(reader.pages) <= 250:
                    raise ValueError("Provide an unencrypted PDF with 1–250 pages")
            except Exception as error:
                raise HTTPException(400, f"Cannot read PDF: {error}") from None
        await run_in_threadpool(validate)
        return await run_in_threadpool(invoke, str(run_id or uuid4()), owner, "start",
                                      paper=base64.b64encode(contents).decode(), filename=paper.filename,
                                      options={"extrapolations": extrapolations, "data_attempts": data_attempts,
                                               "workers": workers, "symbols": symbols})
    finally:
        await paper.close()


@router.get("/runs/{run_id}")
def snapshot(run_id: UUID, owner: str = Depends(current_user)):
    return invoke(str(run_id), owner)


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: UUID, owner: str = Depends(current_user)):
    return invoke(str(run_id), owner, "cancel")


@router.get("/runs/{run_id}/report")
def report(run_id: UUID, owner: str = Depends(current_user)):
    return {"markdown": invoke(str(run_id), owner, "artifact", artifact="report.md")["content"]}


@router.get("/runs/{run_id}/download/{artifact}")
def download(run_id: UUID, artifact: str, owner: str = Depends(current_user)):
    allowed = {"report": ("report.md", "text/markdown"), "results": ("results.json", "application/json"),
               "plan": ("plan.json", "application/json")}
    if artifact not in allowed:
        raise HTTPException(404, "Unknown artifact")
    name, content_type = allowed[artifact]
    content = invoke(str(run_id), owner, "artifact", artifact=name)["content"]
    return Response(content, media_type=content_type,
                    headers={"Content-Disposition": f'attachment; filename="jimmy-{run_id}-{name}"'})


@router.get("/runs/{run_id}/experiments/{experiment_id}/series")
def series(run_id: UUID, experiment_id: str, owner: str = Depends(current_user)):
    return invoke(str(run_id), owner, "series", experiment=experiment_id)["rows"]


app = create_app(router, workflow_id="paper_replication", title="Paper replication")
handler = Mangum(app, lifespan="off")
