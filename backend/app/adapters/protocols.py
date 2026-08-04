from typing import Any, Protocol

from app.domain.models import (
    AlertRef,
    Evidence,
    NormalizedAlert,
    ScenarioSummary,
    ToolDescriptor,
)


class XDRAdapter(Protocol):
    def list_scenarios(self) -> list[ScenarioSummary]: ...

    def get_alert(self, alert_ref: AlertRef) -> NormalizedAlert: ...

    def get_evidence(self, alert: NormalizedAlert) -> list[Evidence]: ...


class ResponseAdapter(Protocol):
    def dry_run(self, descriptor: ToolDescriptor, plan: dict[str, Any]) -> dict[str, Any]: ...

    def execute(self, descriptor: ToolDescriptor, plan: dict[str, Any]) -> dict[str, Any]: ...

    def verify(
        self,
        descriptor: ToolDescriptor,
        plan: dict[str, Any],
        execution: dict[str, Any],
    ) -> dict[str, Any]: ...

    def rollback(
        self,
        descriptor: ToolDescriptor,
        plan: dict[str, Any],
        execution: dict[str, Any],
    ) -> dict[str, Any]: ...
