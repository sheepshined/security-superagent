import asyncio
import hashlib
import json
import sqlite3
from threading import RLock
from uuid import uuid4

from app.adapters.models import DataClassification
from app.agents.triage.graph import TriageGraphRunner
from app.domain.enums import JobStatus, ModelHealth, RunMode
from app.domain.models import (
    JobAccepted,
    JobAuditEvent,
    JobView,
    TriageJobRequest,
    TriageResult,
    utc_now,
)
from app.persistence.migrations import migrate
from app.persistence.sqlite import create_sqlite_connection


class JobNotFoundError(LookupError):
    pass


class ResultNotReadyError(RuntimeError):
    pass


class IdempotencyConflictError(ValueError):
    pass


class JobRepository:
    """SQLite-backed Job, Case, Result and audit-event repository."""

    def __init__(self, database_url: str = "sqlite:///:memory:") -> None:
        self._connection = create_sqlite_connection(database_url)
        self._lock = RLock()
        self._setup()

    def _setup(self) -> None:
        with self._lock:
            migrate(self._connection)

    def create_or_get(
        self,
        request: TriageJobRequest,
        idempotency_key: str | None = None,
    ) -> tuple[JobView, bool]:
        normalized_key = idempotency_key.strip() if idempotency_key else None
        if normalized_key is not None and not 1 <= len(normalized_key) <= 128:
            raise ValueError("Idempotency-Key must contain 1-128 characters")
        request_json, request_hash = self._serialize_request(request)
        with self._lock, self._connection:
            if normalized_key:
                existing = self._connection.execute(
                    "SELECT * FROM triage_jobs WHERE idempotency_key = ?",
                    (normalized_key,),
                ).fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise IdempotencyConflictError(normalized_key)
                    return self._to_view(existing), False
            return self._create_job(
                request=request,
                request_json=request_json,
                request_hash=request_hash,
                idempotency_key=normalized_key,
            ), True

    def create_rerun(self, parent_job_id: str) -> JobView:
        with self._lock, self._connection:
            parent_row = self._connection.execute(
                "SELECT * FROM triage_jobs WHERE job_id = ?",
                (parent_job_id,),
            ).fetchone()
            if parent_row is None:
                raise JobNotFoundError(parent_job_id)
            request = TriageJobRequest.model_validate_json(parent_row["request_json"])
            request_json, request_hash = self._serialize_request(request)
            version_row = self._connection.execute(
                "SELECT COALESCE(MAX(analysis_version), 0) AS version "
                "FROM triage_jobs WHERE case_id = ?",
                (parent_row["case_id"],),
            ).fetchone()
            return self._create_job(
                request=request,
                request_json=request_json,
                request_hash=request_hash,
                idempotency_key=None,
                case_id=parent_row["case_id"],
                analysis_version=int(version_row["version"]) + 1,
                parent_job_id=parent_job_id,
                workflow_version_id=(
                    request.options.workflow_version_id or parent_row["workflow_version_id"]
                ),
            )

    def _create_job(
        self,
        *,
        request: TriageJobRequest,
        request_json: str,
        request_hash: str,
        idempotency_key: str | None,
        case_id: str | None = None,
        analysis_version: int = 1,
        parent_job_id: str | None = None,
        workflow_version_id: str | None = None,
    ) -> JobView:
        now = utc_now()
        is_new_case = case_id is None
        view = JobView(
            job_id=f"job_{uuid4().hex}",
            case_id=case_id or f"case_{uuid4().hex}",
            request_id=f"req_{uuid4().hex}",
            trace_id=f"tr_{uuid4().hex}",
            status=JobStatus.QUEUED,
            progress=0,
            current_node="queued",
            analysis_version=analysis_version,
            parent_job_id=parent_job_id,
            workflow_version_id=(
                workflow_version_id or request.options.workflow_version_id or "workflow_default_v1"
            ),
            created_at=now,
            updated_at=now,
        )
        if is_new_case:
            self._connection.execute(
                """
                INSERT INTO triage_cases(case_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (view.case_id, view.status.value, now.isoformat(), now.isoformat()),
            )
        try:
            self._connection.execute(
                """
                INSERT INTO triage_jobs(
                    job_id, case_id, request_id, trace_id,
                    alert_source, alert_external_id, request_json, request_hash,
                    idempotency_key, analysis_version, parent_job_id, workflow_version_id,
                    status, progress, current_node, error_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    view.job_id,
                    view.case_id,
                    view.request_id,
                    view.trace_id,
                    request.alert_ref.source,
                    request.alert_ref.external_id,
                    request_json,
                    request_hash,
                    idempotency_key,
                    view.analysis_version,
                    view.parent_job_id,
                    view.workflow_version_id,
                    view.status.value,
                    view.progress,
                    view.current_node,
                    view.error_code,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        except sqlite3.IntegrityError:
            if is_new_case:
                self._connection.execute(
                    "DELETE FROM triage_cases WHERE case_id = ?",
                    (view.case_id,),
                )
            if idempotency_key:
                existing = self._connection.execute(
                    "SELECT * FROM triage_jobs WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None and existing["request_hash"] == request_hash:
                    return self._to_view(existing)
                if existing is not None:
                    raise IdempotencyConflictError(idempotency_key) from None
            raise
        self._append_event(
            view,
            "job_created",
            {
                "analysis_version": view.analysis_version,
                "parent_job_id": view.parent_job_id,
                "workflow_version_id": view.workflow_version_id,
            },
        )
        return view

    @staticmethod
    def _serialize_request(request: TriageJobRequest) -> tuple[str, str]:
        payload = request.model_dump(mode="json")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return request.model_dump_json(), hashlib.sha256(canonical.encode()).hexdigest()

    def get(self, job_id: str) -> JobView:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM triage_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            return self._to_view(row)

    def get_by_trace_id(self, trace_id: str) -> JobView:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM triage_jobs WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(trace_id)
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
            self._append_event(
                updated,
                "job_status_changed",
                {
                    "progress": updated.progress,
                    "error_code": updated.error_code,
                },
            )
            return updated

    def append_event(
        self,
        job_id: str,
        event_type: str,
        *,
        current_node: str | None = None,
        data: dict[str, object] | None = None,
    ) -> JobAuditEvent:
        with self._lock, self._connection:
            view = self.get(job_id)
            if current_node is not None:
                view = view.model_copy(update={"current_node": current_node})
            event_id = self._append_event(view, event_type, data)
            return JobAuditEvent(
                event_id=event_id,
                job_id=view.job_id,
                event_type=event_type,
                status=view.status,
                current_node=view.current_node,
                occurred_at=utc_now(),
                data=data or {},
            )

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

    def list_audit_events(
        self,
        job_id: str,
        *,
        after_event_id: int = 0,
    ) -> list[JobAuditEvent]:
        with self._lock:
            if not self._job_exists(job_id):
                raise JobNotFoundError(job_id)
            rows = self._connection.execute(
                """
                SELECT event_id, job_id, event_type, status, current_node, occurred_at,
                       data_json
                FROM job_audit_events
                WHERE job_id = ? AND event_id > ? ORDER BY event_id
                """,
                (job_id, after_event_id),
            ).fetchall()
            return [
                JobAuditEvent(
                    event_id=int(row["event_id"]),
                    job_id=row["job_id"],
                    event_type=row["event_type"],
                    status=JobStatus(row["status"]),
                    current_node=row["current_node"],
                    occurred_at=row["occurred_at"],
                    data=json.loads(row["data_json"]),
                )
                for row in rows
            ]

    def recoverable_jobs(self) -> list[tuple[JobView, TriageJobRequest]]:
        with self._lock, self._connection:
            rows = self._connection.execute(
                """
                SELECT * FROM triage_jobs
                WHERE status IN (?, ?, ?)
                ORDER BY created_at
                """,
                (
                    JobStatus.QUEUED.value,
                    JobStatus.RUNNING.value,
                    JobStatus.RETRYING.value,
                ),
            ).fetchall()
            recovered: list[tuple[JobView, TriageJobRequest]] = []
            for row in rows:
                view = self._to_view(row)
                if view.status != JobStatus.QUEUED:
                    view = self.update(
                        view.job_id,
                        status=JobStatus.RETRYING,
                        progress=view.progress,
                        current_node="recovered_after_restart",
                    )
                recovered.append((view, TriageJobRequest.model_validate_json(row["request_json"])))
            return recovered

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

    def _append_event(
        self,
        view: JobView,
        event_type: str,
        data: dict[str, object] | None = None,
    ) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO job_audit_events(
                job_id, event_type, status, current_node, occurred_at, data_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                view.job_id,
                event_type,
                view.status.value,
                view.current_node,
                utc_now().isoformat(),
                json.dumps(data or {}, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _to_view(row: sqlite3.Row) -> JobView:
        return JobView(
            job_id=row["job_id"],
            case_id=row["case_id"],
            request_id=row["request_id"],
            trace_id=row["trace_id"],
            analysis_version=int(row["analysis_version"]),
            parent_job_id=row["parent_job_id"],
            workflow_version_id=row["workflow_version_id"],
            status=JobStatus(row["status"]),
            progress=int(row["progress"]),
            current_node=row["current_node"],
            error_code=row["error_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class JobRuntime:
    def __init__(
        self,
        repository: JobRepository,
        graph_runner: TriageGraphRunner,
        *,
        hard_timeout_seconds: int = 120,
    ) -> None:
        self._repository = repository
        self._graph_runner = graph_runner
        self._hard_timeout_seconds = hard_timeout_seconds
        self._queue: asyncio.Queue[tuple[str, TriageJobRequest] | None] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._enqueued: set[str] = set()

    @property
    def is_running(self) -> bool:
        return self._worker_task is not None and not self._worker_task.done()

    async def start(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        for job, request in self._repository.recoverable_jobs():
            self._enqueue(job.job_id, request)
        self._worker_task = asyncio.create_task(
            self._worker_loop(),
            name="security-superagent-job-worker",
        )

    async def stop(self) -> None:
        task = self._worker_task
        if task is None:
            return
        await self._queue.put(None)
        try:
            await asyncio.wait_for(task, timeout=10)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._worker_task = None

    def submit(
        self,
        request: TriageJobRequest,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[JobAccepted, bool]:
        job, created = self._repository.create_or_get(request, idempotency_key)
        if created:
            self._enqueue(job.job_id, request)
        return self._accepted(job), created

    def rerun(self, parent_job_id: str) -> JobAccepted:
        job = self._repository.create_rerun(parent_job_id)
        self._enqueue(job.job_id, self._repository.get_request(job.job_id))
        return self._accepted(job)

    @staticmethod
    def _accepted(job: JobView) -> JobAccepted:
        accepted = JobAccepted(
            job_id=job.job_id,
            case_id=job.case_id,
            status=job.status,
            request_id=job.request_id,
            trace_id=job.trace_id,
            analysis_version=job.analysis_version,
            parent_job_id=job.parent_job_id,
            workflow_version_id=job.workflow_version_id,
        )
        return accepted

    def _enqueue(self, job_id: str, request: TriageJobRequest) -> None:
        if job_id in self._enqueued:
            return
        self._enqueued.add(job_id)
        self._queue.put_nowait((job_id, request))

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            job_id, request = item
            try:
                await self.process(job_id, request)
            finally:
                self._enqueued.discard(job_id)
                self._queue.task_done()

    async def process(self, job_id: str, request: TriageJobRequest) -> None:
        current = self._repository.get(job_id)
        if current.status in {JobStatus.SUCCEEDED, JobStatus.DEGRADED}:
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
            result = await asyncio.wait_for(
                self._graph_runner.arun(
                    job_id=job_id,
                    case_id=current.case_id,
                    trace_id=current.trace_id,
                    alert_ref=request.alert_ref,
                    data_classification=data_classification,
                    progress_hook=lambda event_type, node, progress, data: self._record_progress(
                        job_id,
                        event_type=event_type,
                        node=node,
                        progress=progress,
                        data=data,
                    ),
                ),
                timeout=self._hard_timeout_seconds,
            )
            self._repository.save_result(job_id, result)
            final_status = (
                JobStatus.DEGRADED
                if result.model_health == ModelHealth.DEGRADED
                else JobStatus.SUCCEEDED
            )
            self._repository.update(
                job_id,
                status=final_status,
                progress=100,
                current_node=(
                    "completed_degraded" if final_status == JobStatus.DEGRADED else "completed"
                ),
            )
        except TimeoutError:
            self._repository.update(
                job_id,
                status=JobStatus.FAILED,
                progress=100,
                current_node="failed",
                error_code="TRIAGE_TIMEOUT",
            )
        except Exception as exc:
            self._repository.append_event(
                job_id,
                "job_error",
                current_node="failed",
                data={"error_type": type(exc).__name__},
            )
            self._repository.update(
                job_id,
                status=JobStatus.FAILED,
                progress=100,
                current_node="failed",
                error_code="TRIAGE_FAILED",
            )

    def _record_progress(
        self,
        job_id: str,
        *,
        event_type: str,
        node: str,
        progress: int,
        data: dict[str, object],
    ) -> None:
        current = self._repository.get(job_id)
        self._repository.update(
            job_id,
            status=JobStatus.RUNNING,
            progress=max(current.progress, min(progress, 95)),
            current_node=node,
        )
        self._repository.append_event(
            job_id,
            event_type,
            current_node=node,
            data=data,
        )
