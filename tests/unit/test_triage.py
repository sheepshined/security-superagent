import pytest
from app.adapters.mock_xdr import MockXDRAdapter
from app.domain.enums import Verdict
from app.domain.models import AlertRef
from app.rag.hybrid import HybridRetriever
from app.services.triage import TriageService


@pytest.mark.parametrize(
    ("external_id", "expected"),
    [
        ("A-TRI-001", Verdict.TRUE_POSITIVE),
        ("A-TRI-002", Verdict.FALSE_POSITIVE),
        ("A-TRI-003", Verdict.NEEDS_REVIEW),
    ],
)
def test_triage_expected_verdicts(external_id: str, expected: Verdict) -> None:
    service = TriageService(MockXDRAdapter())

    result = service.run(
        job_id="job_test",
        case_id="case_test",
        alert_ref=AlertRef(source="mock-xdr", external_id=external_id),
    )

    assert result.verdict == expected
    assert all(finding.evidence_ids for finding in result.facts)


def test_high_risk_action_is_dry_run_and_requires_approval() -> None:
    service = TriageService(MockXDRAdapter(), HybridRetriever())

    result = service.run(
        job_id="job_test",
        case_id="case_test",
        alert_ref=AlertRef(source="mock-xdr", external_id="A-TRI-001"),
    )

    assert result.requires_superagent is True
    assert result.recommended_actions
    action = result.recommended_actions[0]
    assert action.dry_run is True
    assert action.requires_approval is True
    assert result.citations
    assert any("attack.mitre.org" in citation.source_uri for citation in result.citations)


def test_policy_defaults_to_review_without_a_supported_conclusion() -> None:
    assert TriageService._decide_verdict(set(), set()) == Verdict.NEEDS_REVIEW
