from app.adapters.mock_xdr import MockXDRAdapter
from app.domain.models import AlertRef


def test_mock_xdr_lists_expected_scenarios() -> None:
    adapter = MockXDRAdapter()

    scenarios = adapter.list_scenarios()

    assert {item.scenario_id for item in scenarios} == {
        "TRI-001",
        "TRI-002",
        "TRI-003",
        "SUP-001",
        "SUP-002",
    }


def test_mock_xdr_adds_integrity_hashes() -> None:
    adapter = MockXDRAdapter()
    alert = adapter.get_alert(AlertRef(source="mock-xdr", external_id="A-TRI-001"))

    evidence = adapter.get_evidence(alert)

    assert evidence
    assert all(len(item.integrity_hash) == 64 for item in evidence)
