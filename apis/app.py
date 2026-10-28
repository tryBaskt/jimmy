"""Factory loads one service in Lambda; local development hosts all service routers."""

import importlib
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from apis.context import ServiceContext
from paperlab.config import Settings
from paperlab.storage import make_store

SERVICES = ("projects", "research", "data", "artifacts", "activity", "identity")


def create_app(settings=None, store=None, service=None):
    settings = settings or Settings.load()
    store = store or make_store(settings)
    service = service or os.getenv("API_SERVICE", "all")
    if service not in (*SERVICES, "all"):
        raise ValueError("Unknown API service")
    app = FastAPI(title=f"Jimmy {service} API", version="0.1.0")
    app.state.store, app.state.settings, app.state.service = store, settings, service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.origin],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type"],
    )
    context = ServiceContext(settings, store)
    for name in SERVICES if service == "all" else (service,):
        app.include_router(importlib.import_module(f"apis.{name}.routes").create_router(context))
    return app


app = create_app()
handler = Mangum(app, lifespan="off")
