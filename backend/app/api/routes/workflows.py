from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import (
    get_workflow_service,
    require_admin,
    require_authenticated,
    require_csrf,
)
from app.domain.models import (
    UserView,
    WorkflowTemplateView,
    WorkflowValidationResult,
    WorkflowVersionCreate,
    WorkflowVersionView,
)
from app.services.workflows import (
    WorkflowNotFoundError,
    WorkflowService,
    WorkflowValidationError,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])
WorkflowServiceDependency = Annotated[WorkflowService, Depends(get_workflow_service)]
CurrentUser = Annotated[UserView, Depends(require_authenticated)]
AdminUser = Annotated[UserView, Depends(require_admin)]


def _raise_workflow_error(exc: Exception) -> None:
    if isinstance(exc, WorkflowNotFoundError):
        raise HTTPException(status_code=404, detail="workflow not found") from exc
    if isinstance(exc, WorkflowValidationError):
        raise HTTPException(
            status_code=422,
            detail=exc.result.model_dump(mode="json"),
        ) from exc
    raise exc


@router.get("/templates", response_model=list[WorkflowTemplateView])
def list_templates(
    service: WorkflowServiceDependency,
    _: CurrentUser,
) -> list[WorkflowTemplateView]:
    return service.templates()


@router.get("/versions", response_model=list[WorkflowVersionView])
def list_versions(
    service: WorkflowServiceDependency,
    _: CurrentUser,
    template_id: Annotated[str | None, Query()] = None,
) -> list[WorkflowVersionView]:
    return service.repository.list(template_id)


@router.post(
    "/{template_id}/versions",
    response_model=WorkflowVersionView,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_version(
    template_id: str,
    request: WorkflowVersionCreate,
    service: WorkflowServiceDependency,
    user: AdminUser,
) -> WorkflowVersionView:
    try:
        return service.create_version(template_id, request.config, user)
    except Exception as exc:
        _raise_workflow_error(exc)
        raise


@router.post(
    "/versions/{workflow_version_id}/validate",
    response_model=WorkflowValidationResult,
    dependencies=[Depends(require_csrf)],
)
def validate_version(
    workflow_version_id: str,
    service: WorkflowServiceDependency,
    _: AdminUser,
) -> WorkflowValidationResult:
    try:
        return service.validate_version(workflow_version_id)
    except Exception as exc:
        _raise_workflow_error(exc)
        raise


@router.post(
    "/versions/{workflow_version_id}/activate",
    response_model=WorkflowVersionView,
    dependencies=[Depends(require_csrf)],
)
def activate_version(
    workflow_version_id: str,
    service: WorkflowServiceDependency,
    _: AdminUser,
) -> WorkflowVersionView:
    try:
        return service.activate(workflow_version_id)
    except Exception as exc:
        _raise_workflow_error(exc)
        raise
