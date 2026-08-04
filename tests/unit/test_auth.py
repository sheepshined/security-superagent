import pytest
from app.domain.enums import UserRole
from app.domain.models import UserCreate, UserPatch
from app.services.auth import (
    AuthenticationError,
    AuthorizationError,
    AuthRepository,
    AuthService,
    UserConflictError,
    UserNotFoundError,
)
from argon2 import PasswordHasher, Type


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


def test_authentication_failures_and_bootstrap_are_closed_by_default() -> None:
    repository = AuthRepository()
    service = AuthService(repository)

    assert service.bootstrap_admin("", "missing-username-password") is None
    admin = service.bootstrap_admin("admin-test", "correct-horse-battery-staple")
    assert admin is not None
    assert service.bootstrap_admin("second-admin", "another-secure-password") is None

    for password in ("wrong-password", "security-superagent-dummy-password"):
        with pytest.raises(AuthenticationError):
            service.login("missing-user", password)
    with pytest.raises(AuthenticationError):
        service.authenticate(None)
    with pytest.raises(AuthenticationError):
        service.authenticate("unknown-session")
    with pytest.raises(AuthorizationError):
        service.require_csrf(None, None)
    with pytest.raises(AuthenticationError):
        service.require_csrf("unknown-session", "unknown-csrf")
    service.logout(None)

    duplicate = UserCreate(
        username="admin-test",
        password="another-secure-password",
        role=UserRole.ADMIN,
    )
    with pytest.raises(UserConflictError):
        service.create_user(duplicate)
    with pytest.raises(UserNotFoundError):
        service.update_user("usr_missing", UserPatch(role=UserRole.ANALYST))


def test_password_rotation_invalid_hash_and_legacy_rehash() -> None:
    repository = AuthRepository()
    service = AuthService(repository)
    admin = service.bootstrap_admin("admin-test", "correct-horse-battery-staple")
    assert admin is not None

    unchanged = service.update_user(admin.user_id, UserPatch(role=UserRole.ADMIN))
    assert unchanged.is_active is True
    service.update_user(admin.user_id, UserPatch(password="rotated-admin-password"))
    with pytest.raises(AuthenticationError):
        service.login("admin-test", "correct-horse-battery-staple")
    assert service.login("admin-test", "rotated-admin-password").user.user_id == admin.user_id

    legacy_hasher = PasswordHasher(
        time_cost=1,
        memory_cost=8192,
        parallelism=1,
        hash_len=16,
        salt_len=8,
        type=Type.ID,
    )
    legacy_request = UserCreate(
        username="legacy-user",
        password="legacy-user-password",
        role=UserRole.ANALYST,
    )
    legacy_hash = legacy_hasher.hash(legacy_request.password)
    repository.create_user(legacy_request, legacy_hash)
    assert service.login("legacy-user", legacy_request.password).user.username == "legacy-user"
    refreshed = repository.get_user_with_hash("legacy-user")
    assert refreshed is not None
    assert refreshed[1] != legacy_hash

    invalid_request = UserCreate(
        username="invalid-hash-user",
        password="invalid-hash-password",
        role=UserRole.ANALYST,
    )
    repository.create_user(invalid_request, "not-an-argon2-hash")
    with pytest.raises(AuthenticationError):
        service.login("invalid-hash-user", invalid_request.password)
