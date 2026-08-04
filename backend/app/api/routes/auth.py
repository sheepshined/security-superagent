from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.dependencies import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    AuthServiceDependency,
    require_admin,
    require_authenticated,
    require_csrf,
)
from app.core.config import get_settings
from app.domain.models import LoginRequest, UserCreate, UserPatch, UserView
from app.services.auth import (
    AuthenticationError,
    UserConflictError,
    UserNotFoundError,
)

router = APIRouter(tags=["auth"])
CurrentUser = Annotated[UserView, Depends(require_authenticated)]
AdminUser = Annotated[UserView, Depends(require_admin)]


def _set_auth_cookies(response: Response, token: str, csrf_token: str) -> None:
    settings = get_settings()
    max_age = settings.session_hours * 60 * 60
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    response.delete_cookie(
        CSRF_COOKIE,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


@router.post("/auth/login", response_model=UserView)
def login(
    request: LoginRequest,
    response: Response,
    auth: AuthServiceDependency,
) -> UserView:
    if not auth.is_initialized:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="local administrator is not initialized",
        )
    try:
        grant = auth.login(request.username, request.password)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="invalid credentials") from exc
    _set_auth_cookies(response, grant.token, grant.csrf_token)
    return grant.user


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def logout(
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> None:
    auth.logout(request.cookies.get(SESSION_COOKIE))
    _clear_auth_cookies(response)


@router.get("/auth/me", response_model=UserView)
def me(user: CurrentUser) -> UserView:
    return user


@router.get("/admin/users", response_model=list[UserView])
def list_users(_: AdminUser, auth: AuthServiceDependency) -> list[UserView]:
    return auth.list_users()


@router.post(
    "/admin/users",
    response_model=UserView,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_user(
    request: UserCreate,
    _: AdminUser,
    auth: AuthServiceDependency,
) -> UserView:
    try:
        return auth.create_user(request)
    except UserConflictError as exc:
        raise HTTPException(status_code=409, detail="username already exists") from exc


@router.patch(
    "/admin/users/{user_id}",
    response_model=UserView,
    dependencies=[Depends(require_csrf)],
)
def update_user(
    user_id: str,
    request: UserPatch,
    _: AdminUser,
    auth: AuthServiceDependency,
) -> UserView:
    try:
        return auth.update_user(user_id, request)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail="user not found") from exc
