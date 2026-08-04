import asyncio
import sqlite3
from pathlib import Path

import pytest
from app.adapters.mock_xdr import MockXDRAdapter
from app.domain.enums import JobStatus
from app.domain.models import AlertRef, TriageJobRequest, utc_now
from app.persistence.migrations import LATEST_SCHEMA_VERSION
from app.runtime.jobs import JobRepository, JobRuntime, ResultNotReadyError
from app.services.triage import TriageService


def database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_job_case_request_result_and_events_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    request = TriageJobRequest(alert_ref=AlertRef(source="mock-xdr", external_id="A-TRI-001"))
    repository = JobRepository(database_url(path))
    job, created = repository.create_or_get(request, "restart-test-key")
    assert created is True
    with pytest.raises(ResultNotReadyError):
        repository.get_result(job.job_id)

    repository.update(
        job.job_id,
        status=JobStatus.RUNNING,
        progress=20,
        current_node="langgraph_triage",
    )
    result = TriageService(MockXDRAdapter()).run(
        job_id=job.job_id,
        case_id=job.case_id,
        alert_ref=request.alert_ref,
    )
    repository.save_result(job.job_id, result)
    repository.update(
        job.job_id,
        status=JobStatus.SUCCEEDED,
        progress=100,
        current_node="completed",
    )
    assert repository.audit_event_count(job.job_id) == 4
    repository.close()

    restored = JobRepository(database_url(path))
    duplicate, duplicate_created = restored.create_or_get(request, "restart-test-key")

    assert duplicate_created is False
    assert duplicate.job_id == job.job_id
    assert duplicate.case_id == job.case_id
    assert duplicate.status == JobStatus.SUCCEEDED
    assert restored.get_request(job.job_id) == request
    assert restored.get_result(job.job_id).verdict == result.verdict
    assert restored.audit_event_count(job.job_id) == 4
    assert [event.event_type for event in restored.list_audit_events(job.job_id)] == [
        "job_created",
        "job_status_changed",
        "result_saved",
        "job_status_changed",
    ]
    restored.close()


def test_legacy_database_is_migrated_without_losing_jobs(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    request = TriageJobRequest(alert_ref=AlertRef(source="mock-xdr", external_id="A-TRI-001"))
    now = utc_now().isoformat()
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE triage_cases (
            case_id TEXT PRIMARY KEY, status TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE triage_jobs (
            job_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES triage_cases(case_id),
            request_id TEXT NOT NULL UNIQUE,
            trace_id TEXT NOT NULL UNIQUE,
            alert_source TEXT NOT NULL,
            alert_external_id TEXT NOT NULL,
            request_json TEXT NOT NULL,
            status TEXT NOT NULL,
            progress INTEGER NOT NULL,
            current_node TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(alert_source, alert_external_id)
        );
        CREATE TABLE triage_results (
            job_id TEXT PRIMARY KEY REFERENCES triage_jobs(job_id),
            result_json TEXT NOT NULL, completed_at TEXT NOT NULL
        );
        CREATE TABLE job_audit_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES triage_jobs(job_id),
            event_type TEXT NOT NULL, status TEXT NOT NULL,
            current_node TEXT, occurred_at TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO triage_cases VALUES (?, ?, ?, ?)",
        ("case_legacy", "SUCCEEDED", now, now),
    )
    connection.execute(
        """
        INSERT INTO triage_jobs VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            "job_legacy",
            "case_legacy",
            "req_legacy",
            "tr_legacy",
            "mock-xdr",
            "A-TRI-001",
            request.model_dump_json(),
            "SUCCEEDED",
            100,
            "completed",
            None,
            now,
            now,
        ),
    )
    connection.commit()
    connection.close()

    repository = JobRepository(database_url(path))
    legacy = repository.get("job_legacy")
    new_job, created = repository.create_or_get(request)
    assert legacy.analysis_version == 1
    assert legacy.workflow_version_id == "workflow_default_v1"
    assert created is True
    assert new_job.job_id != legacy.job_id
    repository.close()

    verify = sqlite3.connect(path)
    assert (
        verify.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        == LATEST_SCHEMA_VERSION
    )
    assert verify.execute("SELECT COUNT(*) FROM triage_jobs").fetchone()[0] == 2
    verify.close()


class _SlowGraphRunner:
    async def arun(self, **kwargs: object):
        del kwargs
        await asyncio.sleep(0.05)
        raise AssertionError("hard timeout should cancel graph execution")


@pytest.mark.asyncio
async def test_job_hard_timeout_fails_closed() -> None:
    repository = JobRepository()
    runtime = JobRuntime(repository, _SlowGraphRunner(), hard_timeout_seconds=0.01)  # type: ignore[arg-type]
    request = TriageJobRequest(alert_ref=AlertRef(source="mock-xdr", external_id="A-TRI-001"))
    accepted, _ = runtime.submit(request)

    await runtime.process(accepted.job_id, request)

    job = repository.get(accepted.job_id)
    assert job.status == JobStatus.FAILED
    assert job.error_code == "TRIAGE_TIMEOUT"
