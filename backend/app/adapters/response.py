import hashlib
import json
from typing import Any

from app.domain.models import ToolDescriptor, utc_now


class MockResponseAdapter:
    """Deterministic simulation adapter; it never changes an endpoint or identity system."""

    name = "mock-response"

    def dry_run(self, descriptor: ToolDescriptor, plan: dict[str, Any]) -> dict[str, Any]:
        target = str(plan.get("target", "")).strip()
        conflicts: list[str] = []
        if not target or target == "unknown-host":
            conflicts.append("target is not a concrete normalized entity")
        return {
            "adapter": self.name,
            "mode": "simulation",
            "passed": not conflicts,
            "target": target,
            "tool_name": descriptor.name,
            "conflicts": conflicts,
            "rollback_supported": True,
            "checked_at": utc_now().isoformat(),
        }

    def execute(self, descriptor: ToolDescriptor, plan: dict[str, Any]) -> dict[str, Any]:
        operation_id = (
            "mockop_"
            + hashlib.sha256(
                json.dumps(plan, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()[:24]
        )
        return {
            "adapter": self.name,
            "mode": "simulation",
            "operation_id": operation_id,
            "status": "succeeded",
            "tool_name": descriptor.name,
            "target": plan["target"],
            "executed_at": utc_now().isoformat(),
        }

    def verify(
        self,
        descriptor: ToolDescriptor,
        plan: dict[str, Any],
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        succeeded = execution.get("status") == "succeeded"
        return {
            "adapter": self.name,
            "mode": "simulation",
            "verified": succeeded,
            "operation_id": execution.get("operation_id"),
            "observed_target": plan["target"],
            "observed_state": "isolated" if descriptor.name == "edr.isolate_host" else "applied",
            "verified_at": utc_now().isoformat(),
        }

    def rollback(
        self,
        descriptor: ToolDescriptor,
        plan: dict[str, Any],
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "mode": "simulation",
            "rolled_back": True,
            "operation_id": execution.get("operation_id"),
            "target": plan["target"],
            "tool_name": descriptor.name,
            "rolled_back_at": utc_now().isoformat(),
        }
