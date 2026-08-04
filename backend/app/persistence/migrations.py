import sqlite3
from collections.abc import Callable

from app.domain.models import utc_now

LATEST_SCHEMA_VERSION = 3


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _record(connection: sqlite3.Connection, version: int, name: str) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
        VALUES (?, ?, ?)
        """,
        (version, name, utc_now().isoformat()),
    )


def _create_base_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS triage_cases (
            case_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS triage_jobs (
            job_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES triage_cases(case_id),
            request_id TEXT NOT NULL UNIQUE,
            trace_id TEXT NOT NULL UNIQUE,
            alert_source TEXT NOT NULL,
            alert_external_id TEXT NOT NULL,
            request_json TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            idempotency_key TEXT,
            analysis_version INTEGER NOT NULL DEFAULT 1 CHECK(analysis_version >= 1),
            parent_job_id TEXT REFERENCES triage_jobs(job_id),
            workflow_version_id TEXT NOT NULL DEFAULT 'workflow_default_v1',
            status TEXT NOT NULL,
            progress INTEGER NOT NULL CHECK(progress BETWEEN 0 AND 100),
            current_node TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS triage_results (
            job_id TEXT PRIMARY KEY REFERENCES triage_jobs(job_id),
            result_json TEXT NOT NULL,
            completed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS job_audit_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES triage_jobs(job_id),
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            current_node TEXT,
            occurred_at TEXT NOT NULL,
            data_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_triage_jobs_idempotency_key
            ON triage_jobs(idempotency_key) WHERE idempotency_key IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_triage_jobs_trace_id ON triage_jobs(trace_id);
        CREATE INDEX IF NOT EXISTS idx_triage_jobs_alert
            ON triage_jobs(alert_source, alert_external_id, analysis_version);
        CREATE INDEX IF NOT EXISTS idx_job_audit_events_job_id
            ON job_audit_events(job_id, event_id);
        """
    )


def _upgrade_jobs_v2(connection: sqlite3.Connection) -> None:
    if "idempotency_key" in _columns(connection, "triage_jobs"):
        return

    connection.commit()
    connection.execute("PRAGMA foreign_keys=OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.executescript(
            """
            CREATE TABLE triage_jobs_v2 (
                job_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES triage_cases(case_id),
                request_id TEXT NOT NULL UNIQUE,
                trace_id TEXT NOT NULL UNIQUE,
                alert_source TEXT NOT NULL,
                alert_external_id TEXT NOT NULL,
                request_json TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                idempotency_key TEXT,
                analysis_version INTEGER NOT NULL DEFAULT 1 CHECK(analysis_version >= 1),
                parent_job_id TEXT REFERENCES triage_jobs(job_id),
                workflow_version_id TEXT NOT NULL DEFAULT 'workflow_default_v1',
                status TEXT NOT NULL,
                progress INTEGER NOT NULL CHECK(progress BETWEEN 0 AND 100),
                current_node TEXT,
                error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            INSERT INTO triage_jobs_v2(
                job_id, case_id, request_id, trace_id,
                alert_source, alert_external_id, request_json, request_hash,
                idempotency_key, analysis_version, parent_job_id, workflow_version_id,
                status, progress, current_node, error_code, created_at, updated_at
            )
            SELECT
                job_id, case_id, request_id, trace_id,
                alert_source, alert_external_id, request_json, request_id,
                NULL, 1, NULL, 'workflow_default_v1',
                status, progress, current_node, error_code, created_at, updated_at
            FROM triage_jobs;

            DROP TABLE triage_jobs;
            ALTER TABLE triage_jobs_v2 RENAME TO triage_jobs;
            """
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")

    if "data_json" not in _columns(connection, "job_audit_events"):
        connection.execute(
            "ALTER TABLE job_audit_events ADD COLUMN data_json TEXT NOT NULL DEFAULT '{}'"
        )
    _create_base_tables(connection)


def _create_auth_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS auth_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(user_id),
            csrf_token_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            revoked_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
            ON auth_sessions(user_id, expires_at);
        """
    )


Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    connection.commit()

    if not _table_exists(connection, "triage_jobs"):
        _create_base_tables(connection)
        _record(connection, 1, "initial_job_schema")
        _record(connection, 2, "versioned_jobs_and_explicit_idempotency")
    else:
        _record(connection, 1, "initial_job_schema")
        _upgrade_jobs_v2(connection)
        _record(connection, 2, "versioned_jobs_and_explicit_idempotency")

    _create_auth_tables(connection)
    _record(connection, 3, "local_rbac_sessions")
    connection.commit()

    version = connection.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
    ).fetchone()["version"]
    if int(version) != LATEST_SCHEMA_VERSION:
        raise RuntimeError(f"unexpected database schema version: {version}")
