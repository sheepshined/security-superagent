from pathlib import Path

import pytest
from app.adapters.mock_xdr import MockXDRAdapter
from app.adapters.response import MockResponseAdapter
from app.domain.enums import UserRole
from app.domain.models import (
    ActionDecisionRequest,
    ActionProposalRequest,
    AlertRef,
    TriageJobRequest,
    UserView,
    utc_now,
)
from app.runtime.jobs import JobRepository
from app.services.actions import (
    ActionAuthorizationError,
    ActionIdempotencyConflictError,
    ActionRepository,
    ActionService,
    ActionStateError,
)
from app.services.triage import TriageService
from app.tools.registry import DEFAULT_TOOL_REGISTRY


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _user(user_id: str, role: UserRole) -> UserView:
    now = utc_now()
    return UserView(
        user_id=user_id,
        username=user_id,
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _service(path: Path) -> tuple[ActionService, str]:
    database_url = _database_url(path)
    jobs = JobRepository(database_url)
    job, _ = jobs.create_or_get(
        TriageJobRequest(alert_ref=AlertRef(source="mock-xdr", external_id="A-TRI-001"))
    )
    result = TriageService(MockXDRAdapter()).run(
        job_id=job.job_id,
        case_id=job.case_id,
        alert_ref=AlertRef(source="mock-xdr", external_id="A-TRI-001"),
    )
    jobs.save_result(job.job_id, result)
    return (
        ActionService(
            ActionRepository(database_url),
            jobs,
            DEFAULT_TOOL_REGISTRY,
            MockResponseAdapter(),
        ),
        job.job_id,
    )


def test_high_risk_action_requires_independent_approval_and_verification(
    tmp_path: Path,
) -> None:
    service, job_id = _service(tmp_path / "actions.sqlite3")
    analyst = _user("analyst-one", UserRole.ANALYST)
    approver = _user("approver-one", UserRole.APPROVER)

    proposed = service.propose(
        ActionProposalRequest(job_id=job_id),
        analyst,
        idempotency_key="action-lifecycle",
    )
    assert proposed.status.value == "PROPOSED"
    pending = service.dry_run(proposed.action_id, analyst)
    assert pending.status.value == "PENDING_APPROVAL"
    assert pending.dry_run_result is not None
    assert pending.dry_run_result["mode"] == "simulation"
    assert pending.plan_hash == proposed.plan_hash

    with pytest.raises(ActionAuthorizationError):
        service.decide(
            proposed.action_id,
            ActionDecisionRequest(comment="self approval is forbidden"),
            analyst,
            approve=True,
        )

    approved = service.decide(
        proposed.action_id,
        ActionDecisionRequest(comment="approved for synthetic exercise"),
        approver,
        approve=True,
    )
    assert approved.status.value == "APPROVED"
    assert approved.approvals[0].reviewer_id == approver.user_id

    executed = service.execute(proposed.action_id, approver)
    assert executed.status.value == "SUCCEEDED"
    assert executed.execution_result is not None
    assert executed.execution_result["adapter"] == "mock-response"

    verified = service.verify(proposed.action_id, analyst)
    assert verified.status.value == "VERIFIED"
    assert verified.verification_result is not None
    assert verified.verification_result["verified"] is True
    events = service.repository.list_events(proposed.action_id)
    assert [event.event_type for event in events] == [
        "action_proposed",
        "action_dry_run_passed",
        "action_approval_requested",
        "action_approved",
        "action_execution_started",
        "action_execution_succeeded",
        "action_verified",
    ]


def test_action_idempotency_and_state_machine_reject_invalid_reuse(tmp_path: Path) -> None:
    service, job_id = _service(tmp_path / "idempotency.sqlite3")
    analyst = _user("analyst-one", UserRole.ANALYST)
    request = ActionProposalRequest(job_id=job_id)

    first = service.propose(request, analyst, idempotency_key="same-action")
    duplicate = service.propose(request, analyst, idempotency_key="same-action")

    assert duplicate.action_id == first.action_id
    second_job, _ = service.jobs.create_or_get(
        TriageJobRequest(alert_ref=AlertRef(source="mock-xdr", external_id="A-TRI-001"))
    )
    second_result = TriageService(MockXDRAdapter()).run(
        job_id=second_job.job_id,
        case_id=second_job.case_id,
        alert_ref=AlertRef(source="mock-xdr", external_id="A-TRI-001"),
    )
    service.jobs.save_result(second_job.job_id, second_result)
    with pytest.raises(ActionIdempotencyConflictError):
        service.propose(
            ActionProposalRequest(job_id=second_job.job_id),
            analyst,
            idempotency_key="same-action",
        )
    with pytest.raises(ActionStateError):
        service.execute(first.action_id, analyst)


def test_rejected_action_cannot_execute(tmp_path: Path) -> None:
    service, job_id = _service(tmp_path / "reject.sqlite3")
    analyst = _user("analyst-one", UserRole.ANALYST)
    approver = _user("approver-one", UserRole.APPROVER)
    action = service.propose(
        ActionProposalRequest(job_id=job_id),
        analyst,
        idempotency_key=None,
    )
    service.dry_run(action.action_id, analyst)
    rejected = service.decide(
        action.action_id,
        ActionDecisionRequest(comment="business conflict"),
        approver,
        approve=False,
    )

    assert rejected.status.value == "REJECTED"
    with pytest.raises(ActionStateError):
        service.execute(action.action_id, approver)
