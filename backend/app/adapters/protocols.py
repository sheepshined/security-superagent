from typing import Protocol

from app.domain.models import AlertRef, Evidence, NormalizedAlert, ScenarioSummary


class XDRAdapter(Protocol):
    def list_scenarios(self) -> list[ScenarioSummary]: ...

    def get_alert(self, alert_ref: AlertRef) -> NormalizedAlert: ...

    def get_evidence(self, alert: NormalizedAlert) -> list[Evidence]: ...
