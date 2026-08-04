import sqlite3
from threading import RLock
from uuid import uuid4

from app.adapters.models import DataClassification
from app.agents.triage.graph import TriageGraphRunner
from app.domain.enums import JobStatus, RunMode
from app.domain.models import (
    JobAccepted,
    JobAuditEvent,
    JobView,
    TriageJobRequest,
    TriageResult,
    utc_now,
)
from app.persistence.sqlite import create_sqlite_connection


class JobNotFoundError(LookupError):
    pass


class ResultNotReadyError(RuntimeError):
    pass


class JobRepository:
    """SQLite-backed Job, Case, Result and audit-event repository."""

    def __init__(self, database_url: str = "sqlite:///:memory:") -> None:
        self._connection = create_sqlite_connection(database_url)
        self._lock = RLock()
        self._setup()

    def _setup(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
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
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL CHECK(progress BETWEEN 0 AND 100),
                    current_node TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(alert_source, alert_external_id)
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
                    occurred_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_triage_jobs_trace_id
                    ON triage_jobs(trace_id);
                CREATE INDEX IF NOT EXISTS idx_job_audit_events_job_id
                    ON job_audit_events(job_id, event_id);
                """
            )

    def create_or_get(self, request: TriageJobRequest) -> tuple[JobView, bool]:
        with self._lock, self._connection:
            existing = self._connection.execute(
                """
                SELECT * FROM triage_jobs
                WHERE alert_source = ? AND alert_external_id = ?
                """,
                (request.alert_ref.source, request.alert_ref.external_id),
            ).fetchone()
            if existing is not None:
                return self._to_view(existing), False

            now = utc_now()
            view = JobView(
                job_id=f"job_{uuid4().hex}",
                case_id=f"case_{uuid4().hex}",
                request_id=f"req_{uuid4().hex}",
                trace_id=f"tr_{uuid4().hex}",
                status=JobStatus.QUEUED,
                progress=0,
                current_node="queued",
                created_at=now,
                updated_at=now,
            )
            try:
                self._connection.execute(
                    """
                    INSERT INTO triage_cases(case_id, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        view.case_id,
                        view.status.value,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                self._connection.execute(
                    """
                    INSERT INTO triage_jobs(
                        job_id, case_id, request_id, trace_id,
                        alert_source, alert_external_id, request_json,
                        status, progress, current_node, error_code,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        view.job_id,
                        view.case_id,
                        view.request_id,
                        view.trace_id,
                        request.alert_ref.source,
                        request.alert_ref.external_id,
                        request.model_dump_json(),
                        view.status.value,
                        view.progress,
                        view.current_node,
                        view.error_code,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                self._append_event(view, "job_created")
            except sqlite3.IntegrityError:
                self._connection.execute(
                    "DELETE FROM triage_cases WHERE case_id = ?",
                    (view.case_id,),
                )
                existing = self._connection.execute(
                    """
                    SELECT * FROM triage_jobs
                    WHERE alert_source = ? AND alert_external_id = ?
                    """,
                    (request.alert_ref.source, request.alert_ref.external_id),
                ).fetchone()
                if existing is None:
                    raise
                return self._to_view(existing), False
            return view, True

    def get(self, job_id: str) -> JobView:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM triage_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            return self._to_view(row)

    def get_request(self, job_id: str) -> TriageJobRequest:
        with self._lock:
            row = self._connection.execute(
                "SELECT request_json FROM triage_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            return TriageJobRequest.model_validate_json(row["request_json"])

    def update(
        self,
        job_id: str,
        *,
        status: JobStatus,
        progress: int,
        current_node: str | None,
        error_code: str | None = None,
    ) -> JobView:
        with self._lock, self._connection:
            current = self.get(job_id)
            updated = current.model_copy(
                update={
                    "status": status,
                    "progress": progress,
                    "current_node": current_node,
                    "error_code": error_code,
                    "updated_at": utc_now(),
                }
            )
            self._connection.execute(
                """
                UPDATE triage_jobs
                SET status = ?, progress = ?, current_node = ?, error_code = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    updated.status.value,
                    updated.progress,
                    updated.current_node,
                    updated.error_code,
                    updated.updated_at.isoformat(),
                    job_id,
                ),
            )
            self._connection.execute(
                """
                UPDATE triage_cases SET status = ?, updated_at = ? WHERE case_id = ?
                """,
                (
                    updated.status.value,
                    updated.updated_at.isoformat(),
                    updated.case_id,
                ),
            )
            self._append_event(updated, "job_status_changed")
            return updated

    def save_result(self, job_id: str, result: TriageResult) -> None:
        with self._lock, self._connection:
            job = self.get(job_id)
            self._connection.execute(
                """
                INSERT INTO triage_results(job_id, result_json, completed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    result_json = excluded.result_json,
                    completed_at = excluded.completed_at
                """,
                (job_id, result.model_dump_json(), result.completed_at.isoformat()),
            )
            self._append_event(job, "result_saved")

    def get_result(self, job_id: str) -> TriageResult:
        with self._lock:
            if not self._job_exists(job_id):
                raise JobNotFoundError(job_id)
            row = self._connection.execute(
                "SELECT result_json FROM triage_results WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ResultNotReadyError(job_id)
            return TriageResult.model_validate_json(row["result_json"])

    def audit_event_count(self, job_id: str) -> int:
        with self._lock:
            if not self._job_exists(job_id):
                raise JobNotFoundError(job_id)
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM job_audit_events WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return int(row["count"])

    def list_audit_events(self, job_id: str) -> list[JobAuditEvent]:
        with self._lock:
            if not self._job_exists(job_id):
                raise JobNotFoundError(job_id)
            rows = self._connection.execute(
                """
                SELECT event_id, job_id, event_type, status, current_node, occurred_at
                FROM job_audit_events WHERE job_id = ? ORDER BY event_id
                """,
                (job_id,),
            ).fetchall()
            return [
                JobAuditEvent(
                    event_id=int(row["event_id"]),
                    job_id=row["job_id"],
                    event_type=row["event_type"],
                    status=JobStatus(row["status"]),
                    current_node=row["current_node"],
                    occurred_at=row["occurred_at"],
                )
                for row in rows
            ]

    def health(self) -> bool:
        with self._lock:
            row = self._connection.execute("SELECT 1 AS ok").fetchone()
            return row is not None and row["ok"] == 1

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _job_exists(self, job_id: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM triage_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            is not None
        )

    def _append_event(self, view: JobView, event_type: str) -> None:
        self._connection.execute(
            """
            INSERT INTO job_audit_events(
                job_id, event_type, status, current_node, occurred_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                view.job_id,
                event_type,
                view.status.value,
                view.current_node,
                utc_now().isoformat(),
            ),
        )

    @staticmethod
    def _to_view(row: sqlite3.Row) -> JobView:
        return JobView(
            job_id=row["job_id"],
            case_id=row["case_id"],
            request_id=row["request_id"],
            trace_id=row["trace_id"],
            status=JobStatus(row["status"]),
            progress=int(row["progress"]),
            current_node=row["current_node"],
            error_code=row["error_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class JobRuntime:
    def __init__(self, repository: JobRepository, graph_runner: TriageGraphRunner) -> None:
        self._repository = repository
        self._graph_runner = graph_runner

    def submit(self, request: TriageJobRequest) -> tuple[JobAccepted, bool]:
        job, created = self._repository.create_or_get(request)
        accepted = JobAccepted(
            job_id=job.job_id,
            case_id=job.case_id,
            status=job.status,
            request_id=job.request_id,
            trace_id=job.trace_id,
        )
        return accepted, created

    def process(self, job_id: str, request: TriageJobRequest) -> None:
        current = self._repository.get(job_id)
        if current.status == JobStatus.SUCCEEDED:
            return
        try:
            self._repository.update(
                job_id,
                status=JobStatus.RUNNING,
                progress=20,
                current_node="langgraph_triage",
            )
            data_classification = (
                DataClassification.SYNTHETIC
                if request.mode == RunMode.MOCK
                else DataClassification.REAL_SECURITY_DATA
            )
            result = self._graph_runner.run(
                job_id=job_id,
                case_id=current.case_id,
                trace_id=current.trace_id,
                alert_ref=request.alert_ref,
                data_classification=data_classification,
            )
            self._repository.save_result(job_id, result)
            self._repository.update(
                job_id,
                status=JobStatus.SUCCEEDED,
                progress=100,
                current_node="completed",
            )
        except Exception:
            self._repository.update(
                job_id,
                status=JobStatus.FAILED,
                progress=100,
                current_node="failed",
                error_code="TRIAGE_FAILED",
            )
            raise
