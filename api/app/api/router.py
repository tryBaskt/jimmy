from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.strategy import router as strategy_router

router = APIRouter(prefix="/api")
router.include_router(health_router)
router.include_router(strategy_router)
