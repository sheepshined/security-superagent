from app.domain.enums import Severity
from app.domain.models import Evidence, NormalizedAlert

SEVERITY_VALUE = {
    Severity.LOW: 0.20,
    Severity.MEDIUM: 0.45,
    Severity.HIGH: 0.75,
    Severity.CRITICAL: 1.00,
}


def calculate_risk(alert: NormalizedAlert, evidence: list[Evidence]) -> int:
    """Return a deterministic score; the LLM never directly controls the final value."""
    source_severity = SEVERITY_VALUE[alert.severity]
    evidence_strength = sum(item.confidence for item in evidence) / max(len(evidence), 1)
    asset_criticality = 0.80 if any("db" in entity.id for entity in alert.entities) else 0.55
    attack_chain_completeness = _attack_chain_completeness(evidence)
    threat_intel_hit = 0.75 if alert.indicators else 0.20

    score = 100 * (
        0.20 * source_severity
        + 0.30 * evidence_strength
        + 0.20 * asset_criticality
        + 0.20 * attack_chain_completeness
        + 0.10 * threat_intel_hit
    )
    return max(0, min(100, round(score)))


def _attack_chain_completeness(evidence: list[Evidence]) -> float:
    observed_types = {item.type for item in evidence}
    required = {"network_flow", "process_tree", "identity_event"}
    return len(observed_types & required) / len(required)
