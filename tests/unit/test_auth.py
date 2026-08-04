import pytest
from app.domain.enums import UserRole
from app.domain.models import UserCreate, UserPatch
from app.services.auth import (
    AuthenticationError,
    AuthorizationError,
    AuthRepository,
    AuthService,
)


def test_argon2id_login_session_csrf_and_revocation() -> None:
    repository = AuthRepository()
    service = AuthService(repository, session_hours=8)
    admin = service.bootstrap_admin("admin-test", "correct-horse-battery-staple")
    assert admin is not None
    assert admin.role == UserRole.ADMIN

    with pytest.raises(AuthenticationError):
        service.login("admin-test", "wrong-password")

    grant = service.login("admin-test", "correct-horse-battery-staple")
    assert service.authenticate(grant.token).user_id == admin.user_id
    assert service.require_csrf(grant.token, grant.csrf_token).user_id == admin.user_id
    with pytest.raises(AuthorizationError):
        service.require_csrf(grant.token, "wrong-csrf-token")

    service.logout(grant.token)
    with pytest.raises(AuthenticationError):
        service.authenticate(grant.token)


def test_admin_can_create_and_disable_each_role() -> None:
    service = AuthService(AuthRepository())
    service.bootstrap_admin("admin-test", "correct-horse-battery-staple")
    analyst = service.create_user(
        UserCreate(
            username="analyst-one",
            password="analyst-password-123",
            role=UserRole.ANALYST,
        )
    )
    approver = service.create_user(
        UserCreate(
            username="approver-one",
            password="approver-password-123",
            role=UserRole.APPROVER,
        )
    )
    assert {item.role for item in service.list_users()} == {
        UserRole.ADMIN,
        UserRole.ANALYST,
        UserRole.APPROVER,
    }

    service.update_user(analyst.user_id, UserPatch(is_active=False))
    with pytest.raises(AuthenticationError):
        service.login("analyst-one", "analyst-password-123")
    assert approver.is_active is True
