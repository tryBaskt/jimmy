from fastapi import FastAPI

from app.api.router import router
from app.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    application = FastAPI(
        title="Jimmy API",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )
    application.state.settings = settings
    application.include_router(router)
    return application


app = create_app()
