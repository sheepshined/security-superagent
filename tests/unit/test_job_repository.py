from pathlib import Path

import pytest
from app.adapters.mock_xdr import MockXDRAdapter
from app.domain.enums import JobStatus
from app.domain.models import AlertRef, TriageJobRequest
from app.runtime.jobs import JobRepository, ResultNotReadyError
from app.services.triage import TriageService


def database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_job_case_request_result_and_events_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    request = TriageJobRequest(
        alert_ref=AlertRef(source="mock-xdr", external_id="A-TRI-001")
    )
    repository = JobRepository(database_url(path))
    job, created = repository.create_or_get(request)
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
    duplicate, duplicate_created = restored.create_or_get(request)

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
