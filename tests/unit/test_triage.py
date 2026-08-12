import pytest
from app.adapters.mock_xdr import MockXDRAdapter
from app.domain.enums import Verdict
from app.domain.models import AlertRef, Entity, Evidence, utc_now
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


def test_platform_attestation_yields_true_positive() -> None:
    labels = {"threat_define_900", "attack_state_2", "WebShell"}
    verdict = TriageService._decide_verdict(labels, {"network_flow"})
    assert verdict == Verdict.TRUE_POSITIVE


def test_platform_attestation_requires_network_evidence() -> None:
    labels = {"threat_define_900", "attack_state_2"}
    verdict = TriageService._decide_verdict(labels, set())
    assert verdict == Verdict.NEEDS_REVIEW


def test_platform_attestation_keeps_review_for_unclassified_threat() -> None:
    labels = {"threat_define_400", "attack_state_2"}
    verdict = TriageService._decide_verdict(labels, {"network_flow"})
    assert verdict == Verdict.NEEDS_REVIEW


def test_true_positive_confidence_boosted_by_evidence_types() -> None:
    single = [Evidence(
        evidence_id="e1", type="network_flow", source="s", statement="x",
        observed_at=utc_now(), confidence=0.7, raw_ref="r", integrity_hash="a" * 64,
    )]
    multi = [
        Evidence(
            evidence_id="e2", type="network_flow", source="s", statement="x",
            observed_at=utc_now(), confidence=0.7, raw_ref="r", integrity_hash="b" * 64,
        ),
        Evidence(
            evidence_id="e3", type="identity_event", source="s", statement="x",
            observed_at=utc_now(), confidence=0.7, raw_ref="r", integrity_hash="c" * 64,
        ),
    ]
    single_conf = TriageService._confidence(Verdict.TRUE_POSITIVE, single)
    multi_conf = TriageService._confidence(Verdict.TRUE_POSITIVE, multi)
    assert multi_conf > single_conf


def test_actions_ip_source_suggests_block_indicator() -> None:
    entities = [Entity(type="ip", id="192.168.1.10", role="source")]
    actions = TriageService._actions(Verdict.TRUE_POSITIVE, entities)
    assert actions
    assert actions[0].tool_name == "network.block_indicator"
    assert actions[0].target == "192.168.1.10"


def test_actions_unknown_source_yields_no_action() -> None:
    entities = [Entity(type="ip", id="192.168.1.10", role="target")]
    assert TriageService._actions(Verdict.TRUE_POSITIVE, entities) == []
