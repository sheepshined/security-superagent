from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.api.dependencies import get_action_service, require_authenticated, require_csrf
from app.domain.models import (
    ActionAuditEvent,
    ActionDecisionRequest,
    ActionProposalRequest,
    ResponseActionView,
    UserView,
)
from app.runtime.jobs import JobNotFoundError, ResultNotReadyError
from app.services.actions import (
    ActionAuthorizationError,
    ActionIdempotencyConflictError,
    ActionNotFoundError,
    ActionService,
    ActionStateError,
)

router = APIRouter(prefix="/actions", tags=["actions"])
ActionServiceDependency = Annotated[ActionService, Depends(get_action_service)]
CurrentUser = Annotated[UserView, Depends(require_authenticated)]


def _raise_action_error(exc: Exception) -> None:
    if isinstance(exc, (ActionNotFoundError, JobNotFoundError)):
        raise HTTPException(status_code=404, detail="action or job not found") from exc
    if isinstance(exc, ActionAuthorizationError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ResultNotReadyError):
        raise HTTPException(status_code=409, detail="job result is not ready") from exc
    if isinstance(exc, ActionIdempotencyConflictError):
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used for a different action",
        ) from exc
    if isinstance(exc, ActionStateError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


@router.post(
    "",
    response_model=ResponseActionView,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def propose_action(
    request: ActionProposalRequest,
    service: ActionServiceDependency,
    user: CurrentUser,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ResponseActionView:
    try:
        return service.propose(request, user, idempotency_key=idempotency_key)
    except Exception as exc:
        _raise_action_error(exc)
        raise


@router.get("", response_model=list[ResponseActionView])
def list_actions(
    service: ActionServiceDependency,
    _: CurrentUser,
    job_id: Annotated[str | None, Query()] = None,
) -> list[ResponseActionView]:
    return service.list(job_id=job_id)


@router.get("/{action_id}", response_model=ResponseActionView)
def get_action(
    action_id: str,
    service: ActionServiceDependency,
    _: CurrentUser,
) -> ResponseActionView:
    try:
        return service.get(action_id)
    except Exception as exc:
        _raise_action_error(exc)
        raise


@router.get("/{action_id}/events", response_model=list[ActionAuditEvent])
def get_action_events(
    action_id: str,
    service: ActionServiceDependency,
    _: CurrentUser,
) -> list[ActionAuditEvent]:
    try:
        return service.repository.list_events(action_id)
    except Exception as exc:
        _raise_action_error(exc)
        raise


@router.post(
    "/{action_id}/dry-run",
    response_model=ResponseActionView,
    dependencies=[Depends(require_csrf)],
)
def dry_run_action(
    action_id: str,
    service: ActionServiceDependency,
    user: CurrentUser,
) -> ResponseActionView:
    try:
        return service.dry_run(action_id, user)
    except Exception as exc:
        _raise_action_error(exc)
        raise


@router.post(
    "/{action_id}/approve",
    response_model=ResponseActionView,
    dependencies=[Depends(require_csrf)],
)
def approve_action(
    action_id: str,
    request: ActionDecisionRequest,
    service: ActionServiceDependency,
    user: CurrentUser,
) -> ResponseActionView:
    try:
        return service.decide(action_id, request, user, approve=True)
    except Exception as exc:
        _raise_action_error(exc)
        raise


@router.post(
    "/{action_id}/reject",
    response_model=ResponseActionView,
    dependencies=[Depends(require_csrf)],
)
def reject_action(
    action_id: str,
    request: ActionDecisionRequest,
    service: ActionServiceDependency,
    user: CurrentUser,
) -> ResponseActionView:
    try:
        return service.decide(action_id, request, user, approve=False)
    except Exception as exc:
        _raise_action_error(exc)
        raise


@router.post(
    "/{action_id}/execute",
    response_model=ResponseActionView,
    dependencies=[Depends(require_csrf)],
)
def execute_action(
    action_id: str,
    service: ActionServiceDependency,
    user: CurrentUser,
) -> ResponseActionView:
    try:
        return service.execute(action_id, user)
    except Exception as exc:
        _raise_action_error(exc)
        raise


@router.post(
    "/{action_id}/verify",
    response_model=ResponseActionView,
    dependencies=[Depends(require_csrf)],
)
def verify_action(
    action_id: str,
    service: ActionServiceDependency,
    user: CurrentUser,
) -> ResponseActionView:
    try:
        return service.verify(action_id, user)
    except Exception as exc:
        _raise_action_error(exc)
        raise


@router.post(
    "/{action_id}/rollback",
    response_model=ResponseActionView,
    dependencies=[Depends(require_csrf)],
)
def rollback_action(
    action_id: str,
    service: ActionServiceDependency,
    user: CurrentUser,
) -> ResponseActionView:
    try:
        return service.rollback(action_id, user)
    except Exception as exc:
        _raise_action_error(exc)
        raise
