from fastapi import APIRouter, Depends

from app.api.dependencies import require_authenticated
from app.api.routes import actions, auth, health, knowledge, scenarios, tools, triage, workflows

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router, prefix="/api/v1")
protected = [Depends(require_authenticated)]
api_router.include_router(knowledge.router, prefix="/api/v1", dependencies=protected)
api_router.include_router(tools.router, prefix="/api/v1", dependencies=protected)
api_router.include_router(actions.router, prefix="/api/v1", dependencies=protected)
api_router.include_router(workflows.router, prefix="/api/v1", dependencies=protected)
api_router.include_router(scenarios.router, prefix="/api/v1", dependencies=protected)
api_router.include_router(triage.router, prefix="/api/v1", dependencies=protected)
