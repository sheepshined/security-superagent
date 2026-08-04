import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.domain.models import (
    AlertRef,
    Evidence,
    NormalizedAlert,
    ScenarioSummary,
)

DEFAULT_SCENARIO_DIR = Path(__file__).resolve().parents[1] / "data" / "scenarios"


class ScenarioNotFoundError(LookupError):
    pass


class MockXDRAdapter:
    def __init__(self, scenario_dir: Path = DEFAULT_SCENARIO_DIR) -> None:
        self._scenario_dir = scenario_dir

    def _load_documents(self) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        for path in sorted(self._scenario_dir.glob("*.json")):
            documents.append(json.loads(path.read_text(encoding="utf-8")))
        return documents

    def list_scenarios(self) -> list[ScenarioSummary]:
        return [ScenarioSummary.model_validate(item["scenario"]) for item in self._load_documents()]

    def _find(self, external_id: str) -> dict[str, Any]:
        for item in self._load_documents():
            if item["alert"]["external_id"] == external_id:
                return item
        raise ScenarioNotFoundError(f"mock alert not found: {external_id}")

    def get_alert(self, alert_ref: AlertRef) -> NormalizedAlert:
        item = self._find(alert_ref.external_id)
        alert = NormalizedAlert.model_validate(item["alert"])
        if alert.source != alert_ref.source:
            raise ScenarioNotFoundError(
                f"mock alert source mismatch: {alert_ref.source}/{alert_ref.external_id}"
            )
        return alert

    def get_evidence(self, alert: NormalizedAlert) -> list[Evidence]:
        item = self._find(alert.external_id)
        evidence: list[Evidence] = []
        for raw in item["evidence"]:
            document = dict(raw)
            canonical = json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")
            document.setdefault("integrity_hash", hashlib.sha256(canonical).hexdigest())
            document["observed_at"] = datetime.fromisoformat(document["observed_at"])
            evidence.append(Evidence.model_validate(document))
        return evidence
