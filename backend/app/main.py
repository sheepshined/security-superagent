import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.dependencies import get_job_runtime
from app.api.router import api_router
from app.core.config import Settings, get_settings

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    get_settings()
    runtime_provider = application.dependency_overrides.get(
        get_job_runtime,
        get_job_runtime,
    )
    runtime = runtime_provider()
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Evidence-first security triage Agent and SuperAgent API",
        lifespan=lifespan,
        servers=[{"url": settings.public_base_url}] if settings.public_base_url else None,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router)

    @application.exception_handler(RequestValidationError)
    async def log_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # External tool platforms hide the FastAPI detail; log the raw body
        # so schema mismatches on the platform side are diagnosable.
        body = await request.body()
        logger.warning(
            "REQUEST_VALIDATION_ERROR %s %s body=%s errors=%s",
            request.method,
            request.url.path,
            body.decode("utf-8", errors="replace"),
            exc.errors(),
        )
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @application.get("/openapi-external.json", include_in_schema=False)
    def openapi_external() -> dict[str, Any]:
        return _external_openapi_schema(application, get_settings())

    return application


EXTERNAL_OPENAPI_PATHS = frozenset(
    {
        "/healthz",
        "/readyz",
        "/api/v1/scenarios",
        "/api/v1/triage/jobs",
        "/api/v1/triage/investigate",
        "/api/v1/jobs/{job_id}",
        "/api/v1/jobs/{job_id}/result",
        "/api/v1/jobs/{job_id}/events",
        "/api/v1/knowledge/search",
        "/api/v1/tools",
    }
)


def _external_openapi_schema(application: FastAPI, settings: Settings) -> dict[str, Any]:
    """Read-only subset of the OpenAPI schema for external tool platforms.

    Write/admin/auth endpoints are never exposed; the apiKey scheme tells
    GPTs-style importers how to authenticate (X-API-Key header).
    """
    schema = application.openapi()
    schema["paths"] = {
        path: ops
        for path, ops in schema.get("paths", {}).items()
        if path in EXTERNAL_OPENAPI_PATHS
    }
    if settings.public_base_url:
        schema["servers"] = [{"url": settings.public_base_url}]
    components = schema.setdefault("components", {})
    components["securitySchemes"] = {
        "XApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
    }
    schema["security"] = [{"XApiKey": []}]
    return schema


app = create_app()
