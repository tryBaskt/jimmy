from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.research import router as research_router

router = APIRouter(prefix="/api")
router.include_router(health_router)
router.include_router(research_router)
