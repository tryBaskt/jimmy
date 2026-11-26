"""Common HTTP setup; workflow services supply their own routers."""
import os
import re
from collections.abc import Callable

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from security.workflow_auth import current_user


def create_app(router: APIRouter, *, workflow_id: str, title: str,
               runtime_configured: Callable[[], bool] | None = None) -> FastAPI:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", workflow_id):
        raise ValueError("Invalid workflow ID")
    prefix = "/api/" + workflow_id.replace("_", "-")
    app = FastAPI(title=title, version="0.1.0")
    app.add_middleware(CORSMiddleware,
        allow_origins=[origin for origin in os.getenv("WEB_ORIGINS", "").split(",") if origin],
        allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Content-Disposition"])

    @app.get(prefix + "/health")
    def health():
        return {"status": "ok", "workflow": workflow_id,
                "runtime_configured": runtime_configured() if runtime_configured else False,
                "model": os.getenv("OPENAI_MODEL", "")}

    @app.get(prefix + "/me")
    def me(owner: str = Depends(current_user)):
        return {"sub": owner}

    app.include_router(router, prefix=prefix, dependencies=[Depends(current_user)])
    return app
