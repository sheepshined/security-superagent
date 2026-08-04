from typing import Annotated

from fastapi import APIRouter, Depends

from app.adapters.mock_xdr import MockXDRAdapter
from app.api.dependencies import (
    get_auth_service,
    get_job_repository,
    get_job_runtime,
    get_knowledge_catalog,
    get_xdr_adapter,
)
from app.core.config import Settings, get_settings
from app.rag.catalog import KnowledgeCatalog
from app.runtime.jobs import JobRepository, JobRuntime
from app.services.auth import AuthService

router = APIRouter(tags=["health"])
SettingsDependency = Annotated[Settings, Depends(get_settings)]
XDRDependency = Annotated[MockXDRAdapter, Depends(get_xdr_adapter)]
RepositoryDependency = Annotated[JobRepository, Depends(get_job_repository)]
RuntimeDependency = Annotated[JobRuntime, Depends(get_job_runtime)]
AuthDependency = Annotated[AuthService, Depends(get_auth_service)]
CatalogDependency = Annotated[KnowledgeCatalog, Depends(get_knowledge_catalog)]


@router.get("/healthz")
def healthz(settings: SettingsDependency) -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "version": "0.1.0"}


@router.get("/readyz")
def readyz(
    settings: SettingsDependency,
    xdr: XDRDependency,
    repository: RepositoryDependency,
    runtime: RuntimeDependency,
    auth: AuthDependency,
    catalog: CatalogDependency,
) -> dict[str, object]:
    scenarios = xdr.list_scenarios()
    database_ready = repository.health()
    active_model = (
        "deepseek"
        if settings.model_provider == "deepseek" and settings.deepseek_api_key
        else "mock"
    )
    knowledge_metrics = catalog.metrics()
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
            "worker": "ready" if runtime.is_running else "not_ready",
            "auth": (
                "ready" if not settings.auth_required or auth.is_initialized else "not_initialized"
            ),
            "chroma": ("ready" if knowledge_metrics.chroma_enabled else "snapshot_fallback"),
            "knowledge_index": knowledge_metrics.active_index_id or "snapshot_fallback",
            "knowledge_chunks": knowledge_metrics.chunk_count,
            "onnx": "ready" if knowledge_metrics.chroma_enabled else "not_cached",
            "mcp": "configured" if settings.mcp_enabled else "disabled",
        },
    }
