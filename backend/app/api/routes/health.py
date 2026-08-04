from typing import Annotated

from fastapi import APIRouter, Depends

from app.adapters.mock_xdr import MockXDRAdapter
from app.api.dependencies import get_job_repository, get_xdr_adapter
from app.core.config import Settings, get_settings
from app.runtime.jobs import JobRepository

router = APIRouter(tags=["health"])
SettingsDependency = Annotated[Settings, Depends(get_settings)]
XDRDependency = Annotated[MockXDRAdapter, Depends(get_xdr_adapter)]
RepositoryDependency = Annotated[JobRepository, Depends(get_job_repository)]


@router.get("/healthz")
def healthz(settings: SettingsDependency) -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "version": "0.1.0"}


@router.get("/readyz")
def readyz(
    settings: SettingsDependency,
    xdr: XDRDependency,
    repository: RepositoryDependency,
) -> dict[str, object]:
    scenarios = xdr.list_scenarios()
    database_ready = repository.health()
    active_model = (
        "deepseek"
        if settings.model_provider == "deepseek" and settings.deepseek_api_key
        else "mock"
    )
    return {
        "status": "ready" if scenarios and database_ready else "not_ready",
        "dependencies": {
            "xdr": settings.xdr_mode,
            "model": active_model,
            "langsmith": (
                "enabled"
                if settings.langsmith_tracing and settings.langsmith_api_key
                else "disabled"
            ),
            "scenario_count": len(scenarios),
            "sqlite": "ready" if database_ready else "not_ready",
        },
    }
