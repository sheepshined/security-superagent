from fastapi import APIRouter

from app.api.routes import health, knowledge, scenarios, triage

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(knowledge.router, prefix="/api/v1")
api_router.include_router(scenarios.router, prefix="/api/v1")
api_router.include_router(triage.router, prefix="/api/v1")
