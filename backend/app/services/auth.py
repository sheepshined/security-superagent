import hashlib
import hmac
import secrets
import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from threading import RLock

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from app.domain.enums import UserRole
from app.domain.models import UserCreate, UserPatch, UserView, utc_now
from app.persistence.migrations import migrate
from app.persistence.sqlite import create_sqlite_connection


class AuthenticationError(PermissionError):
    pass


class AuthorizationError(PermissionError):
    pass


class UserConflictError(ValueError):
    pass


class UserNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class SessionGrant:
    token: str
    csrf_token: str
    expires_at: str
    user: UserView


class AuthRepository:
    def __init__(self, database_url: str = "sqlite:///:memory:") -> None:
        self._connection = create_sqlite_connection(database_url)
        self._lock = RLock()
        with self._lock:
            migrate(self._connection)

    def user_count(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()
            return int(row["count"])

    def create_user(self, request: UserCreate, password_hash: str) -> UserView:
        now = utc_now()
        user_id = f"usr_{secrets.token_hex(16)}"
        with self._lock, self._connection:
            try:
                self._connection.execute(
                    """
                    INSERT INTO users(
                        user_id, username, password_hash, role, is_active,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        user_id,
                        request.username,
                        password_hash,
                        request.role.value,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise UserConflictError(request.username) from exc
            return UserView(
                user_id=user_id,
                username=request.username,
                role=request.role,
                is_active=True,
                created_at=now,
                updated_at=now,
            )

    def get_user_with_hash(self, username: str) -> tuple[UserView, str] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
            if row is None:
                return None
            return self._to_user(row), str(row["password_hash"])

    def list_users(self) -> list[UserView]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM users ORDER BY username COLLATE NOCASE"
            ).fetchall()
            return [self._to_user(row) for row in rows]

    def update_user(
        self,
        user_id: str,
        request: UserPatch,
        password_hash: str | None,
    ) -> UserView:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                raise UserNotFoundError(user_id)
            current = self._to_user(row)
            role = request.role or current.role
            is_active = current.is_active if request.is_active is None else request.is_active
            updated_at = utc_now()
            self._connection.execute(
                """
                UPDATE users
                SET role = ?, is_active = ?, password_hash = COALESCE(?, password_hash),
                    updated_at = ?
                WHERE user_id = ?
                """,
                (
                    role.value,
                    int(is_active),
                    password_hash,
                    updated_at.isoformat(),
                    user_id,
                ),
            )
            if not is_active:
                self._connection.execute(
                    "UPDATE auth_sessions SET revoked_at = ? "
                    "WHERE user_id = ? AND revoked_at IS NULL",
                    (updated_at.isoformat(), user_id),
                )
            return current.model_copy(
                update={
                    "role": role,
                    "is_active": is_active,
                    "updated_at": updated_at,
                }
            )

    def create_session(
        self,
        user: UserView,
        *,
        session_hash: str,
        csrf_hash: str,
        session_hours: int,
    ) -> str:
        now = utc_now()
        expires_at = now + timedelta(hours=session_hours)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO auth_sessions(
                    session_id, user_id, csrf_token_hash, expires_at,
                    created_at, last_seen_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    session_hash,
                    user.user_id,
                    csrf_hash,
                    expires_at.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        return expires_at.isoformat()

    def get_session(self, session_hash: str) -> tuple[UserView, str] | None:
        now = utc_now()
        with self._lock, self._connection:
            row = self._connection.execute(
                """
                SELECT u.*, s.csrf_token_hash, s.expires_at
                FROM auth_sessions s
                JOIN users u ON u.user_id = s.user_id
                WHERE s.session_id = ? AND s.revoked_at IS NULL
                  AND s.expires_at > ? AND u.is_active = 1
                """,
                (session_hash, now.isoformat()),
            ).fetchone()
            if row is None:
                return None
            self._connection.execute(
                "UPDATE auth_sessions SET last_seen_at = ? WHERE session_id = ?",
                (now.isoformat(), session_hash),
            )
            return self._to_user(row), str(row["csrf_token_hash"])

    def revoke_session(self, session_hash: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? "
                "WHERE session_id = ? AND revoked_at IS NULL",
                (utc_now().isoformat(), session_hash),
            )

    def replace_password_hash(self, user_id: str, password_hash: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE user_id = ?",
                (password_hash, utc_now().isoformat(), user_id),
            )

    @staticmethod
    def _to_user(row: sqlite3.Row) -> UserView:
        return UserView(
            user_id=row["user_id"],
            username=row["username"],
            role=UserRole(row["role"]),
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class AuthService:
    def __init__(self, repository: AuthRepository, *, session_hours: int = 8) -> None:
        self._repository = repository
        self._session_hours = session_hours
        self._hasher = PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=2,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )

    @property
    def is_initialized(self) -> bool:
        return self._repository.user_count() > 0

    def bootstrap_admin(self, username: str, password: str) -> UserView | None:
        if self.is_initialized:
            return None
        if not username or not password:
            return None
        request = UserCreate(
            username=username,
            password=password,
            role=UserRole.ADMIN,
        )
        return self._repository.create_user(request, self._hasher.hash(password))

    def login(self, username: str, password: str) -> SessionGrant:
        record = self._repository.get_user_with_hash(username)
        if record is None:
            self._dummy_verify(password)
            raise AuthenticationError("invalid credentials")
        user, password_hash = record
        if not user.is_active:
            raise AuthenticationError("invalid credentials")
        try:
            self._hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError) as exc:
            raise AuthenticationError("invalid credentials") from exc
        if self._hasher.check_needs_rehash(password_hash):
            self._repository.replace_password_hash(
                user.user_id,
                self._hasher.hash(password),
            )
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = self._repository.create_session(
            user,
            session_hash=self._digest(token),
            csrf_hash=self._digest(csrf_token),
            session_hours=self._session_hours,
        )
        return SessionGrant(
            token=token,
            csrf_token=csrf_token,
            expires_at=expires_at,
            user=user,
        )

    def authenticate(self, token: str | None) -> UserView:
        if not token:
            raise AuthenticationError("authentication required")
        record = self._repository.get_session(self._digest(token))
        if record is None:
            raise AuthenticationError("authentication required")
        return record[0]

    def require_csrf(self, token: str | None, csrf_token: str | None) -> UserView:
        if not token or not csrf_token:
            raise AuthorizationError("CSRF validation failed")
        record = self._repository.get_session(self._digest(token))
        if record is None:
            raise AuthenticationError("authentication required")
        user, expected_hash = record
        if not hmac.compare_digest(expected_hash, self._digest(csrf_token)):
            raise AuthorizationError("CSRF validation failed")
        return user

    def logout(self, token: str | None) -> None:
        if token:
            self._repository.revoke_session(self._digest(token))

    def create_user(self, request: UserCreate) -> UserView:
        return self._repository.create_user(
            request,
            self._hasher.hash(request.password),
        )

    def list_users(self) -> list[UserView]:
        return self._repository.list_users()

    def update_user(self, user_id: str, request: UserPatch) -> UserView:
        password_hash = self._hasher.hash(request.password) if request.password else None
        return self._repository.update_user(user_id, request, password_hash)

    def _dummy_verify(self, password: str) -> None:
        dummy = self._hasher.hash("security-superagent-dummy-password")
        with suppress(VerificationError):
            self._hasher.verify(dummy, password)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()
