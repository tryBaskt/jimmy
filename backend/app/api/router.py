from fastapi import APIRouter

from app.api.routes.health import router as health_router

router = APIRouter(prefix="/api")
router.include_router(health_router)
