from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from app.adapters.mock_xdr import MockXDRAdapter
from app.adapters.response import MockResponseAdapter
from app.domain.enums import ActionStatus, ToolRisk, UserRole
from app.domain.models import (
    ActionDecisionRequest,
    ActionProposal,
    ActionProposalRequest,
    AlertRef,
    ToolDescriptor,
    TriageJobRequest,
    UserView,
    utc_now,
)
from app.runtime.jobs import JobRepository
from app.services.actions import (
    ActionAuthorizationError,
    ActionIdempotencyConflictError,
    ActionNotFoundError,
    ActionRepository,
    ActionService,
    ActionStateError,
)
from app.services.triage import TriageService
from app.tools.registry import DEFAULT_TOOL_REGISTRY, ToolRegistry


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


def _replace_proposal(
    service: ActionService,
    job_id: str,
    **updates: Any,
) -> ActionProposal:
    result = service.jobs.get_result(job_id)
    proposal = result.recommended_actions[0].model_copy(update=updates)
    service.jobs.save_result(
        job_id,
        result.model_copy(update={"recommended_actions": [proposal]}),
    )
    return proposal


def _approved_action(
    service: ActionService,
    job_id: str,
    *,
    key: str,
) -> tuple[str, UserView, UserView]:
    analyst = _user(f"analyst-{key}", UserRole.ANALYST)
    approver = _user(f"approver-{key}", UserRole.APPROVER)
    action = service.propose(
        ActionProposalRequest(job_id=job_id),
        analyst,
        idempotency_key=key,
    )
    service.dry_run(action.action_id, analyst)
    service.decide(
        action.action_id,
        ActionDecisionRequest(comment="approved in unit test"),
        approver,
        approve=True,
    )
    return action.action_id, analyst, approver


class ReturningFailureAdapter(MockResponseAdapter):
    def execute(self, descriptor: ToolDescriptor, plan: dict[str, Any]) -> dict[str, Any]:
        return {"status": "failed", "reason": "simulated rejection"}


class RaisingExecuteAdapter(MockResponseAdapter):
    def execute(self, descriptor: ToolDescriptor, plan: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("simulated adapter failure")


class UnverifiedAdapter(MockResponseAdapter):
    def verify(
        self,
        descriptor: ToolDescriptor,
        plan: dict[str, Any],
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        return {"verified": False, "reason": "state was not observed"}


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

    with pytest.raises(ActionAuthorizationError):
        service.execute(proposed.action_id, analyst)
    executed = service.execute(proposed.action_id, approver)
    assert executed.status.value == "SUCCEEDED"
    assert executed.execution_result is not None
    assert executed.execution_result["adapter"] == "mock-response"

    verified = service.verify(proposed.action_id, analyst)
    assert verified.status.value == "VERIFIED"
    assert verified.verification_result is not None
    assert verified.verification_result["verified"] is True
    with pytest.raises(ActionAuthorizationError):
        service.rollback(proposed.action_id, analyst)
    rolled_back = service.rollback(proposed.action_id, approver)
    assert rolled_back.status == ActionStatus.ROLLED_BACK
    events = service.repository.list_events(proposed.action_id)
    assert [event.event_type for event in events] == [
        "action_proposed",
        "action_dry_run_passed",
        "action_approval_requested",
        "action_approved",
        "action_execution_started",
        "action_execution_succeeded",
        "action_verified",
        "action_rolled_back",
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


def test_repository_rejects_invalid_transitions_and_duplicate_decisions(tmp_path: Path) -> None:
    service, job_id = _service(tmp_path / "repository-guards.sqlite3")
    analyst = _user("analyst-repository", UserRole.ANALYST)
    approver = _user("approver-repository", UserRole.APPROVER)
    action = service.propose(
        ActionProposalRequest(job_id=job_id),
        analyst,
        idempotency_key=None,
    )

    assert service.repository.list()
    assert service.repository.list(job_id=job_id)[0].action_id == action.action_id
    with pytest.raises(ActionNotFoundError):
        service.repository.get("act_missing")
    with pytest.raises(ValueError, match="unsupported action result field"):
        service.repository.transition(
            action.action_id,
            expected={ActionStatus.PROPOSED},
            target=ActionStatus.FAILED,
            actor_id=analyst.user_id,
            event_type="invalid",
            result_field="plan_hash",
        )
    with pytest.raises(ActionNotFoundError):
        service.repository.transition(
            "act_missing",
            expected={ActionStatus.PROPOSED},
            target=ActionStatus.FAILED,
            actor_id=analyst.user_id,
            event_type="missing",
        )
    with pytest.raises(ActionStateError):
        service.repository.transition(
            action.action_id,
            expected={ActionStatus.APPROVED},
            target=ActionStatus.EXECUTING,
            actor_id=analyst.user_id,
            event_type="wrong-state",
        )
    with pytest.raises(ActionNotFoundError):
        service.repository.decide(
            "act_missing",
            user=approver,
            decision="approved",
            comment="missing action",
        )
    with pytest.raises(ActionStateError):
        service.repository.decide(
            action.action_id,
            user=approver,
            decision="approved",
            comment="not pending",
        )

    service.dry_run(action.action_id, analyst)
    connection = service.repository._connection
    with connection:
        connection.execute(
            """
            INSERT INTO action_approvals(
                approval_id, action_id, reviewer_id, reviewer_username,
                decision, comment, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "apr_existing",
                action.action_id,
                approver.user_id,
                approver.username,
                "approved",
                "pre-existing decision",
                utc_now().isoformat(),
            ),
        )
    with pytest.raises(ActionStateError, match="already decided"):
        service.repository.decide(
            action.action_id,
            user=approver,
            decision="approved",
            comment="duplicate",
        )


def test_proposal_boundary_rejects_invalid_tool_metadata_and_role(tmp_path: Path) -> None:
    analyst = _user("analyst-boundary", UserRole.ANALYST)

    service, job_id = _service(tmp_path / "bad-index.sqlite3")
    with pytest.raises(ActionStateError, match="does not exist"):
        service.propose(
            ActionProposalRequest(job_id=job_id, proposal_index=1),
            analyst,
            idempotency_key=None,
        )

    service, job_id = _service(tmp_path / "missing-tool.sqlite3")
    _replace_proposal(service, job_id, tool_name="unregistered.response_tool")
    with pytest.raises(ActionStateError, match="not registered"):
        service.propose(
            ActionProposalRequest(job_id=job_id), analyst, idempotency_key=None
        )

    service, job_id = _service(tmp_path / "read-only.sqlite3")
    _replace_proposal(
        service,
        job_id,
        tool_name="query_network_context",
        risk_level=ToolRisk.LOW,
    )
    with pytest.raises(ActionStateError, match="read-only"):
        service.propose(
            ActionProposalRequest(job_id=job_id), analyst, idempotency_key=None
        )

    service, job_id = _service(tmp_path / "risk-mismatch.sqlite3")
    _replace_proposal(service, job_id, risk_level=ToolRisk.CRITICAL)
    with pytest.raises(ActionStateError, match="risk does not match"):
        service.propose(
            ActionProposalRequest(job_id=job_id), analyst, idempotency_key=None
        )

    service, job_id = _service(tmp_path / "role-denied.sqlite3")
    descriptor = DEFAULT_TOOL_REGISTRY.get("edr.isolate_host").model_copy(
        update={"allowed_roles": [UserRole.ADMIN]}
    )
    service.registry = ToolRegistry([descriptor])
    with pytest.raises(ActionAuthorizationError, match="role is not allowed"):
        service.propose(
            ActionProposalRequest(job_id=job_id), analyst, idempotency_key=None
        )


def test_dry_run_failure_optional_approval_and_plan_integrity(tmp_path: Path) -> None:
    analyst = _user("analyst-dry-run", UserRole.ANALYST)

    service, job_id = _service(tmp_path / "dry-run-failure.sqlite3")
    _replace_proposal(service, job_id, target="unknown-host")
    action = service.propose(
        ActionProposalRequest(job_id=job_id), analyst, idempotency_key=None
    )
    failed = service.dry_run(action.action_id, analyst)
    assert failed.status == ActionStatus.FAILED
    assert failed.dry_run_result == {
        **failed.dry_run_result,
        "passed": False,
    }

    service, job_id = _service(tmp_path / "optional-approval.sqlite3")
    descriptor = DEFAULT_TOOL_REGISTRY.get("edr.isolate_host").model_copy(
        update={"requires_approval": False}
    )
    service.registry = ToolRegistry([descriptor])
    action = service.propose(
        ActionProposalRequest(job_id=job_id), analyst, idempotency_key=None
    )
    dry_run_passed = service.dry_run(action.action_id, analyst)
    assert dry_run_passed.status == ActionStatus.DRY_RUN_PASSED

    service, job_id = _service(tmp_path / "tampered-plan.sqlite3")
    action = service.propose(
        ActionProposalRequest(job_id=job_id), analyst, idempotency_key=None
    )
    with service.repository._connection:
        service.repository._connection.execute(
            "UPDATE response_actions SET target = ? WHERE action_id = ?",
            ("tampered-host", action.action_id),
        )
    with pytest.raises(ActionStateError, match="plan hash mismatch"):
        service.dry_run(action.action_id, analyst)


def test_action_expiration_and_expired_proposal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    analyst = _user("analyst-expiration", UserRole.ANALYST)
    service, job_id = _service(tmp_path / "expired-proposal.sqlite3")
    _replace_proposal(service, job_id, expires_at=utc_now() - timedelta(seconds=1))
    with pytest.raises(ActionStateError, match="already expired"):
        service.propose(
            ActionProposalRequest(job_id=job_id), analyst, idempotency_key=None
        )

    service, job_id = _service(tmp_path / "action-expiration.sqlite3")
    expiry = utc_now() + timedelta(minutes=5)
    _replace_proposal(service, job_id, expires_at=expiry)
    action = service.propose(
        ActionProposalRequest(job_id=job_id), analyst, idempotency_key=None
    )
    monkeypatch.setattr("app.services.actions.utc_now", lambda: expiry + timedelta(seconds=1))
    assert service.get(action.action_id).status == ActionStatus.EXPIRED
    assert service.list(job_id=job_id)[0].status == ActionStatus.EXPIRED


def test_execution_and_verification_fail_closed(tmp_path: Path) -> None:
    service, job_id = _service(tmp_path / "returning-failure.sqlite3")
    action_id, _, approver = _approved_action(service, job_id, key="returning-failure")
    service.adapter = ReturningFailureAdapter()
    failed = service.execute(action_id, approver)
    assert failed.status == ActionStatus.FAILED
    assert failed.execution_result == {
        "reason": "simulated rejection",
        "status": "failed",
    }

    service, job_id = _service(tmp_path / "raising-failure.sqlite3")
    action_id, _, approver = _approved_action(service, job_id, key="raising-failure")
    service.adapter = RaisingExecuteAdapter()
    failed = service.execute(action_id, approver)
    assert failed.status == ActionStatus.FAILED
    assert failed.execution_result is not None
    assert failed.execution_result["error"] == "simulated adapter failure"

    service, job_id = _service(tmp_path / "verification-failure.sqlite3")
    action_id, analyst, approver = _approved_action(service, job_id, key="verification-failure")
    service.execute(action_id, approver)
    service.adapter = UnverifiedAdapter()
    failed = service.verify(action_id, analyst)
    assert failed.status == ActionStatus.FAILED
    assert failed.verification_result == {
        "reason": "state was not observed",
        "verified": False,
    }


def test_self_approval_is_denied_even_when_requester_has_approver_role(tmp_path: Path) -> None:
    service, job_id = _service(tmp_path / "self-approval.sqlite3")
    requester = _user("dual-role-user", UserRole.APPROVER)
    action = service.propose(
        ActionProposalRequest(job_id=job_id),
        requester,
        idempotency_key=None,
    )
    service.dry_run(action.action_id, requester)

    with pytest.raises(ActionAuthorizationError, match="own action"):
        service.decide(
            action.action_id,
            ActionDecisionRequest(comment="must be denied"),
            requester,
            approve=True,
        )
