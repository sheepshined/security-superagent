from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import timedelta
from threading import RLock
from typing import Any

from app.adapters.protocols import ResponseAdapter
from app.domain.enums import ActionStatus, ToolRisk, UserRole
from app.domain.models import (
    ActionApproval,
    ActionAuditEvent,
    ActionDecisionRequest,
    ActionProposal,
    ActionProposalRequest,
    ResponseActionView,
    UserView,
    utc_now,
)
from app.persistence.migrations import migrate
from app.persistence.sqlite import create_sqlite_connection
from app.runtime.jobs import JobRepository
from app.tools.registry import ToolRegistry, ToolRegistryError


class ActionNotFoundError(LookupError):
    pass


class ActionStateError(ValueError):
    pass


class ActionAuthorizationError(PermissionError):
    pass


class ActionIdempotencyConflictError(ValueError):
    pass


TERMINAL_ACTION_STATUSES = {
    ActionStatus.REJECTED,
    ActionStatus.FAILED,
    ActionStatus.VERIFIED,
    ActionStatus.ROLLED_BACK,
    ActionStatus.EXPIRED,
}


class ActionRepository:
    def __init__(self, database_url: str = "sqlite:///:memory:") -> None:
        self._connection = create_sqlite_connection(database_url)
        self._lock = RLock()
        with self._lock:
            migrate(self._connection)

    def create(
        self,
        *,
        job_id: str,
        proposal: ActionProposal,
        plan_hash: str,
        request_hash: str,
        idempotency_key: str | None,
        user: UserView,
    ) -> ResponseActionView:
        now = utc_now()
        expires_at = proposal.expires_at or now + timedelta(minutes=30)
        if expires_at <= now:
            raise ActionStateError("action proposal is already expired")
        action_id = f"act_{secrets.token_hex(16)}"
        with self._lock, self._connection:
            if idempotency_key:
                existing = self._connection.execute(
                    "SELECT action_id, request_hash FROM response_actions "
                    "WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise ActionIdempotencyConflictError(idempotency_key)
                    return self.get(str(existing["action_id"]))
            self._connection.execute(
                """
                INSERT INTO response_actions(
                    action_id, job_id, tool_name, target, scope_json, reason,
                    expected_effect, rollback, risk_level, status, plan_hash,
                    request_hash, idempotency_key, requested_by, requested_by_username,
                    dry_run_json, execution_json, verification_json,
                    expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)
                """,
                (
                    action_id,
                    job_id,
                    proposal.tool_name,
                    proposal.target,
                    json.dumps(proposal.scope, ensure_ascii=False, sort_keys=True),
                    proposal.reason,
                    proposal.expected_effect,
                    proposal.rollback,
                    proposal.risk_level.value,
                    ActionStatus.PROPOSED.value,
                    plan_hash,
                    request_hash,
                    idempotency_key,
                    user.user_id,
                    user.username,
                    expires_at.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            self._insert_event(
                action_id=action_id,
                event_type="action_proposed",
                from_status=None,
                to_status=ActionStatus.PROPOSED,
                actor_id=user.user_id,
                data={"plan_hash": plan_hash, "job_id": job_id},
            )
        return self.get(action_id)

    def get(self, action_id: str) -> ResponseActionView:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM response_actions WHERE action_id = ?",
                (action_id,),
            ).fetchone()
            if row is None:
                raise ActionNotFoundError(action_id)
            approvals = self._connection.execute(
                "SELECT * FROM action_approvals WHERE action_id = ? ORDER BY decided_at",
                (action_id,),
            ).fetchall()
            return self._to_view(row, approvals)

    def list(self, *, job_id: str | None = None) -> list[ResponseActionView]:
        with self._lock:
            if job_id:
                rows = self._connection.execute(
                    "SELECT action_id FROM response_actions WHERE job_id = ? "
                    "ORDER BY created_at DESC",
                    (job_id,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT action_id FROM response_actions ORDER BY created_at DESC LIMIT 200"
                ).fetchall()
            return [self.get(str(row["action_id"])) for row in rows]

    def transition(
        self,
        action_id: str,
        *,
        expected: set[ActionStatus],
        target: ActionStatus,
        actor_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        result_field: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> ResponseActionView:
        allowed_fields = {
            "dry_run_json",
            "execution_json",
            "verification_json",
        }
        if result_field and result_field not in allowed_fields:
            raise ValueError("unsupported action result field")
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT status FROM response_actions WHERE action_id = ?",
                (action_id,),
            ).fetchone()
            if row is None:
                raise ActionNotFoundError(action_id)
            current = ActionStatus(row["status"])
            if current not in expected:
                raise ActionStateError(f"cannot transition {current.value} to {target.value}")
            now = utc_now()
            if result_field:
                self._connection.execute(
                    f"UPDATE response_actions SET status = ?, {result_field} = ?, "
                    "updated_at = ? WHERE action_id = ? AND status = ?",
                    (
                        target.value,
                        json.dumps(result or {}, ensure_ascii=False, sort_keys=True),
                        now.isoformat(),
                        action_id,
                        current.value,
                    ),
                )
            else:
                self._connection.execute(
                    "UPDATE response_actions SET status = ?, updated_at = ? "
                    "WHERE action_id = ? AND status = ?",
                    (target.value, now.isoformat(), action_id, current.value),
                )
            self._insert_event(
                action_id=action_id,
                event_type=event_type,
                from_status=current,
                to_status=target,
                actor_id=actor_id,
                data=data or {},
            )
        return self.get(action_id)

    def decide(
        self,
        action_id: str,
        *,
        user: UserView,
        decision: str,
        comment: str,
    ) -> ResponseActionView:
        target = ActionStatus.APPROVED if decision == "approved" else ActionStatus.REJECTED
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT status FROM response_actions WHERE action_id = ?",
                (action_id,),
            ).fetchone()
            if row is None:
                raise ActionNotFoundError(action_id)
            current = ActionStatus(row["status"])
            if current != ActionStatus.PENDING_APPROVAL:
                raise ActionStateError("action is not pending approval")
            now = utc_now()
            try:
                self._connection.execute(
                    """
                    INSERT INTO action_approvals(
                        approval_id, action_id, reviewer_id, reviewer_username,
                        decision, comment, decided_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"apr_{secrets.token_hex(16)}",
                        action_id,
                        user.user_id,
                        user.username,
                        decision,
                        comment,
                        now.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ActionStateError("reviewer already decided this action") from exc
            self._connection.execute(
                "UPDATE response_actions SET status = ?, updated_at = ? "
                "WHERE action_id = ? AND status = ?",
                (target.value, now.isoformat(), action_id, current.value),
            )
            self._insert_event(
                action_id=action_id,
                event_type=f"action_{decision}",
                from_status=current,
                to_status=target,
                actor_id=user.user_id,
                data={"comment": comment},
            )
        return self.get(action_id)

    def list_events(self, action_id: str) -> list[ActionAuditEvent]:
        self.get(action_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM action_audit_events WHERE action_id = ? ORDER BY event_id",
                (action_id,),
            ).fetchall()
            return [
                ActionAuditEvent(
                    event_id=row["event_id"],
                    action_id=row["action_id"],
                    event_type=row["event_type"],
                    from_status=(ActionStatus(row["from_status"]) if row["from_status"] else None),
                    to_status=ActionStatus(row["to_status"]),
                    actor_id=row["actor_id"],
                    occurred_at=row["occurred_at"],
                    data=json.loads(row["data_json"]),
                )
                for row in rows
            ]

    def _insert_event(
        self,
        *,
        action_id: str,
        event_type: str,
        from_status: ActionStatus | None,
        to_status: ActionStatus,
        actor_id: str,
        data: dict[str, Any],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO action_audit_events(
                action_id, event_type, from_status, to_status,
                actor_id, occurred_at, data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                event_type,
                from_status.value if from_status else None,
                to_status.value,
                actor_id,
                utc_now().isoformat(),
                json.dumps(data, ensure_ascii=False, sort_keys=True),
            ),
        )

    @staticmethod
    def _to_view(row: sqlite3.Row, approvals: list[sqlite3.Row]) -> ResponseActionView:
        def load_json(value: str | None) -> dict[str, Any] | None:
            return json.loads(value) if value else None

        return ResponseActionView(
            action_id=row["action_id"],
            job_id=row["job_id"],
            tool_name=row["tool_name"],
            target=row["target"],
            scope=json.loads(row["scope_json"]),
            reason=row["reason"],
            expected_effect=row["expected_effect"],
            rollback=row["rollback"],
            risk_level=ToolRisk(row["risk_level"]),
            status=ActionStatus(row["status"]),
            plan_hash=row["plan_hash"],
            requested_by=row["requested_by"],
            requested_by_username=row["requested_by_username"],
            approvals=[
                ActionApproval(
                    approval_id=item["approval_id"],
                    reviewer_id=item["reviewer_id"],
                    reviewer_username=item["reviewer_username"],
                    decision=item["decision"],
                    comment=item["comment"],
                    decided_at=item["decided_at"],
                )
                for item in approvals
            ],
            dry_run_result=load_json(row["dry_run_json"]),
            execution_result=load_json(row["execution_json"]),
            verification_result=load_json(row["verification_json"]),
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class ActionService:
    def __init__(
        self,
        repository: ActionRepository,
        jobs: JobRepository,
        registry: ToolRegistry,
        adapter: ResponseAdapter,
    ) -> None:
        self.repository = repository
        self.jobs = jobs
        self.registry = registry
        self.adapter = adapter

    def propose(
        self,
        request: ActionProposalRequest,
        user: UserView,
        *,
        idempotency_key: str | None,
    ) -> ResponseActionView:
        result = self.jobs.get_result(request.job_id)
        try:
            proposal = result.recommended_actions[request.proposal_index]
        except IndexError as exc:
            raise ActionStateError("recommended action does not exist") from exc
        try:
            descriptor = self.registry.get(proposal.tool_name)
        except ToolRegistryError as exc:
            raise ActionStateError(str(exc)) from exc
        if not descriptor.side_effects:
            raise ActionStateError("read-only tools do not create response actions")
        if descriptor.risk_level != proposal.risk_level:
            raise ActionStateError("proposal risk does not match the registered descriptor")
        self._require_tool_role(descriptor.allowed_roles, user)
        plan_hash = self._hash_plan(self._proposal_plan(request.job_id, proposal))
        request_hash = hashlib.sha256(request.model_dump_json().encode("utf-8")).hexdigest()
        return self.repository.create(
            job_id=request.job_id,
            proposal=proposal,
            plan_hash=plan_hash,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            user=user,
        )

    def get(self, action_id: str) -> ResponseActionView:
        action = self.repository.get(action_id)
        return self._expire_if_needed(action)

    def list(self, *, job_id: str | None = None) -> list[ResponseActionView]:
        return [self._expire_if_needed(item) for item in self.repository.list(job_id=job_id)]

    def dry_run(self, action_id: str, user: UserView) -> ResponseActionView:
        action = self._mutable(action_id, {ActionStatus.PROPOSED})
        descriptor = self.registry.get(action.tool_name)
        self._require_tool_role(descriptor.allowed_roles, user)
        plan = self._verified_plan(action)
        outcome = self.adapter.dry_run(descriptor, plan)
        target = ActionStatus.DRY_RUN_PASSED if outcome.get("passed") else ActionStatus.FAILED
        action = self.repository.transition(
            action_id,
            expected={ActionStatus.PROPOSED},
            target=target,
            actor_id=user.user_id,
            event_type="action_dry_run_passed"
            if outcome.get("passed")
            else "action_dry_run_failed",
            data={"conflicts": outcome.get("conflicts", [])},
            result_field="dry_run_json",
            result=outcome,
        )
        if target == ActionStatus.DRY_RUN_PASSED and descriptor.requires_approval:
            action = self.repository.transition(
                action_id,
                expected={ActionStatus.DRY_RUN_PASSED},
                target=ActionStatus.PENDING_APPROVAL,
                actor_id=user.user_id,
                event_type="action_approval_requested",
                data={"plan_hash": action.plan_hash},
            )
        return action

    def decide(
        self,
        action_id: str,
        request: ActionDecisionRequest,
        user: UserView,
        *,
        approve: bool,
    ) -> ResponseActionView:
        action = self._mutable(action_id, {ActionStatus.PENDING_APPROVAL})
        if user.role not in {UserRole.APPROVER, UserRole.ADMIN}:
            raise ActionAuthorizationError("approver or administrator role required")
        if action.requested_by == user.user_id:
            raise ActionAuthorizationError("requester cannot approve or reject their own action")
        self._verified_plan(action)
        return self.repository.decide(
            action_id,
            user=user,
            decision="approved" if approve else "rejected",
            comment=request.comment,
        )

    def execute(self, action_id: str, user: UserView) -> ResponseActionView:
        action = self._mutable(action_id, {ActionStatus.APPROVED})
        if user.role not in {UserRole.APPROVER, UserRole.ADMIN}:
            raise ActionAuthorizationError("approver or administrator role required")
        descriptor = self.registry.get(action.tool_name)
        plan = self._verified_plan(action)
        self.repository.transition(
            action_id,
            expected={ActionStatus.APPROVED},
            target=ActionStatus.EXECUTING,
            actor_id=user.user_id,
            event_type="action_execution_started",
            data={"adapter": getattr(self.adapter, "name", "response-adapter")},
        )
        try:
            outcome = self.adapter.execute(descriptor, plan)
            succeeded = outcome.get("status") == "succeeded"
        except Exception as exc:
            outcome = {"status": "failed", "error": str(exc)[:1000]}
            succeeded = False
        return self.repository.transition(
            action_id,
            expected={ActionStatus.EXECUTING},
            target=ActionStatus.SUCCEEDED if succeeded else ActionStatus.FAILED,
            actor_id=user.user_id,
            event_type="action_execution_succeeded" if succeeded else "action_execution_failed",
            result_field="execution_json",
            result=outcome,
        )

    def verify(self, action_id: str, user: UserView) -> ResponseActionView:
        action = self._mutable(action_id, {ActionStatus.SUCCEEDED})
        descriptor = self.registry.get(action.tool_name)
        plan = self._verified_plan(action)
        outcome = self.adapter.verify(descriptor, plan, action.execution_result or {})
        verified = outcome.get("verified") is True
        return self.repository.transition(
            action_id,
            expected={ActionStatus.SUCCEEDED},
            target=ActionStatus.VERIFIED if verified else ActionStatus.FAILED,
            actor_id=user.user_id,
            event_type="action_verified" if verified else "action_verification_failed",
            result_field="verification_json",
            result=outcome,
        )

    def rollback(self, action_id: str, user: UserView) -> ResponseActionView:
        action = self._mutable(
            action_id,
            {ActionStatus.SUCCEEDED, ActionStatus.VERIFIED},
        )
        if user.role not in {UserRole.APPROVER, UserRole.ADMIN}:
            raise ActionAuthorizationError("approver or administrator role required")
        descriptor = self.registry.get(action.tool_name)
        plan = self._verified_plan(action)
        outcome = self.adapter.rollback(descriptor, plan, action.execution_result or {})
        return self.repository.transition(
            action_id,
            expected={ActionStatus.SUCCEEDED, ActionStatus.VERIFIED},
            target=ActionStatus.ROLLED_BACK,
            actor_id=user.user_id,
            event_type="action_rolled_back",
            data=outcome,
        )

    def _mutable(
        self,
        action_id: str,
        statuses: set[ActionStatus],
    ) -> ResponseActionView:
        action = self._expire_if_needed(self.repository.get(action_id))
        if action.status not in statuses:
            raise ActionStateError(f"action status is {action.status.value}")
        return action

    def _expire_if_needed(self, action: ResponseActionView) -> ResponseActionView:
        if action.status not in TERMINAL_ACTION_STATUSES and action.expires_at <= utc_now():
            return self.repository.transition(
                action.action_id,
                expected={action.status},
                target=ActionStatus.EXPIRED,
                actor_id="system",
                event_type="action_expired",
            )
        return action

    @staticmethod
    def _proposal_plan(job_id: str, proposal: ActionProposal) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "tool_name": proposal.tool_name,
            "target": proposal.target,
            "scope": proposal.scope,
            "reason": proposal.reason,
            "expected_effect": proposal.expected_effect,
            "rollback": proposal.rollback,
            "risk_level": proposal.risk_level.value,
        }

    def _verified_plan(self, action: ResponseActionView) -> dict[str, Any]:
        plan = {
            "job_id": action.job_id,
            "tool_name": action.tool_name,
            "target": action.target,
            "scope": action.scope,
            "reason": action.reason,
            "expected_effect": action.expected_effect,
            "rollback": action.rollback,
            "risk_level": action.risk_level.value,
        }
        if self._hash_plan(plan) != action.plan_hash:
            raise ActionStateError("immutable action plan hash mismatch")
        return plan

    @staticmethod
    def _hash_plan(plan: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                plan,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _require_tool_role(allowed: list[UserRole], user: UserView) -> None:
        if user.role not in allowed:
            raise ActionAuthorizationError("role is not allowed to use this tool")
