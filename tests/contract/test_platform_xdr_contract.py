"""Contract tests for PlatformXDRAdapter against a mocked Sangfor platform.

These tests do not touch a real platform; they lock the adapter's auth
header, field mapping, pagination, error handling and data classification
contracts so the Mock main line can be swapped safely later.
"""

import json

import httpx
import pytest
from app.adapters.platform_xdr import (
    PlatformXDRAdapter,
    XdrAlertNotFoundError,
    XdrApiError,
)
from app.adapters.xdr_schemas import AlertItem, map_severity, to_normalized_alert
from app.domain.enums import Severity
from app.domain.models import AlertRef

BASE_URL = "https://xdr.test.local"


def _adapter(handler, **kwargs) -> PlatformXDRAdapter:
    return PlatformXDRAdapter(
        base_url=BASE_URL,
        access_key="AKTEST123456",
        secret_key="SKTEST123456",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


SAMPLE_ALERT = {
    "uuId": "alert-abc123",
    "name": "异常东西向 SMB 连接",
    "severity": 55,
    "firstTime": 1681142400,
    "lastTime": 1681142460,
    "srcIp": "192.168.1.10",
    "dstIp": ["10.0.0.5", "10.0.0.6"],
    "hostIp": "192.168.1.10",
    "srcPort": 445,
    "dstPort": 445,
    "url": "",
    "domain": "evil.example.com",
    "fileMd5": "d41d8cd98f00b204e9800998ecf8427e",
    "exploitCveId": "CVE-2017-0144",
    "threatClassDesc": "入侵攻击",
    "threatTypeDesc": "横向移动",
    "attackState": 2,
    "threatDefine": [450],
    "description": "办公终端在非工作时段访问多台服务器。",
}


def _alert_list_response(items: list[dict]) -> dict:
    return {
        "code": "Success",
        "message": "请求成功",
        "data": {"total": len(items), "page": 1, "pageSize": 10, "item": items},
    }


def _proof_response(proof_type: str, fields: dict) -> dict:
    return {
        "code": "Success",
        "message": "成功",
        "data": {
            "uuId": "alert-abc123",
            "name": "异常东西向 SMB 连接",
            "severity": 55,
            "lastTime": 1681142460,
            "srcIp": "192.168.1.10",
            "dstIp": "10.0.0.5",
            # The real platform returns proof as a flat field dictionary
            # plus a proofType list (verified against XDR 2.0.20).
            "proofType": [proof_type],
            "proof": fields,
        },
    }


def _normalized_alert() -> object:
    return to_normalized_alert(
        AlertItem.model_validate(SAMPLE_ALERT),
        source="sangfor-xdr",
    )


def test_list_scenarios_returns_mapped_summaries() -> None:
    adapter = _adapter(
        lambda request: httpx.Response(200, json=_alert_list_response([SAMPLE_ALERT]))
    )
    scenarios = adapter.list_scenarios()
    assert len(scenarios) == 1
    assert scenarios[0].scenario_id == "alert-abc123"
    assert scenarios[0].severity == Severity.HIGH


def test_get_alert_maps_platform_fields() -> None:
    adapter = _adapter(
        lambda request: httpx.Response(200, json=_alert_list_response([SAMPLE_ALERT]))
    )
    alert = adapter.get_alert(AlertRef(source="sangfor-xdr", external_id="alert-abc123"))
    assert alert.external_id == "alert-abc123"
    assert alert.title == "异常东西向 SMB 连接"
    assert alert.severity == Severity.HIGH  # (50,70] -> high
    assert alert.occurred_at.tzinfo is not None
    entity_ids = {(item.type, item.role, item.id) for item in alert.entities}
    assert ("ip", "source", "192.168.1.10") in entity_ids
    assert ("ip", "target", "10.0.0.5") in entity_ids
    assert ("host", "asset", "192.168.1.10") in entity_ids
    indicator_values = {item.value for item in alert.indicators}
    assert {
        "evil.example.com",
        "CVE-2017-0144",
        "d41d8cd98f00b204e9800998ecf8427e",
    } <= indicator_values
    assert "横向移动" in alert.labels
    assert alert.raw_ref.startswith("xdr://")


def test_get_alert_sends_uuids_in_post_body() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["body"] = json.loads(request.content) if request.content else {}
        return httpx.Response(200, json=_alert_list_response([SAMPLE_ALERT]))

    adapter = _adapter(handler)
    adapter.get_alert(AlertRef(source="sangfor-xdr", external_id="alert-abc123"))
    assert captured["method"] == "POST"
    assert captured["body"]["uuIds"] == ["alert-abc123"]
    assert captured["body"]["pageSize"] == 5


def test_get_alert_sends_authorization_header() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json=_alert_list_response([SAMPLE_ALERT]))

    adapter = _adapter(handler)
    adapter.get_alert(AlertRef(source="sangfor-xdr", external_id="alert-abc123"))
    assert captured["authorization"].startswith("algorithm=HMAC-SHA256, Access=")
    assert "Signature=" in captured["authorization"]


def test_get_evidence_maps_proof_types() -> None:
    adapter = _adapter(
        lambda request: httpx.Response(
            200, json=_proof_response("smb", {"smbUserName": "admin", "smbStatus": 1})
        )
    )
    evidence = adapter.get_evidence(_normalized_alert())
    assert evidence
    assert evidence[0].type == "network_flow"
    assert evidence[0].access_label == "platform"
    assert len(evidence[0].integrity_hash) == 64


def test_get_evidence_unclassified_becomes_telemetry_gap() -> None:
    adapter = _adapter(
        lambda request: httpx.Response(
            200, json=_proof_response("customProtocol", {"rawData": "x"})
        )
    )
    evidence = adapter.get_evidence(_normalized_alert())
    assert evidence
    assert evidence[0].type == "telemetry_gap"


def test_error_code_raises_domain_error() -> None:
    adapter = _adapter(
        lambda request: httpx.Response(
            200, json={"code": "Failed", "message": "权限不足", "data": None}
        )
    )
    with pytest.raises(XdrApiError):
        adapter.list_scenarios()


def test_alert_not_found_raises_lookup_error() -> None:
    adapter = _adapter(
        lambda request: httpx.Response(200, json=_alert_list_response([]))
    )
    with pytest.raises(XdrAlertNotFoundError):
        adapter.get_alert(AlertRef(source="sangfor-xdr", external_id="missing"))


def test_retry_after_transient_status() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(503, json={"code": "Error", "data": None})
        return httpx.Response(200, json=_alert_list_response([SAMPLE_ALERT]))

    adapter = _adapter(handler)
    scenarios = adapter.list_scenarios()
    assert len(scenarios) == 1
    assert calls["count"] == 2


def test_transport_error_retries_then_fails() -> None:
    class BrokenTransport(httpx.MockTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

    adapter = PlatformXDRAdapter(
        base_url=BASE_URL,
        access_key="AKTEST123456",
        secret_key="SKTEST123456",
        max_retries=1,
        transport=BrokenTransport(lambda request: None),
    )
    with pytest.raises(XdrApiError):
        adapter.list_scenarios()


def test_empty_proof_becomes_telemetry_gap() -> None:
    adapter = _adapter(
        lambda request: httpx.Response(
            200,
            json={
                "code": "Success",
                "message": "ok",
                "data": {"uuId": "alert-abc123", "name": "n", "proofType": [], "proof": {}},
            },
        )
    )
    evidence = adapter.get_evidence(_normalized_alert())
    assert evidence
    assert evidence[0].type == "telemetry_gap"


def test_ts_to_dt_handles_milliseconds() -> None:
    from app.adapters.xdr_schemas import ts_to_dt

    dt = ts_to_dt(1681142460000)
    assert dt.year == 2023


def test_incidents_data_source_uses_incident_endpoints() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_alert_list_response([SAMPLE_ALERT]))

    adapter = _adapter(handler, data_source="incidents")
    adapter.list_scenarios()
    assert "/api/xdr/v1/incidents/list" in captured["url"]


INCIDENT_SAMPLE = {
    "uuId": "incident-xyz",
    "name": "主机存在SQL注入横向行为",
    "incidentSeverity": 3,
    "startTime": 1681142400,
    "hostIp": "10.67.201.61",
    "riskTag": ["SQL注入"],
    "alertIds": ["alert-linked-1"],
    "description": "内网横向攻击",
}


def test_incident_severity_maps_enum() -> None:
    from app.adapters.xdr_schemas import map_incident_severity

    assert map_incident_severity(None) == Severity.LOW
    assert map_incident_severity(0) == Severity.LOW
    assert map_incident_severity(1) == Severity.LOW
    assert map_incident_severity(2) == Severity.MEDIUM
    assert map_incident_severity(3) == Severity.HIGH
    assert map_incident_severity(4) == Severity.CRITICAL


def test_incident_get_alert_uses_incident_fields() -> None:
    adapter = _adapter(
        lambda request: httpx.Response(200, json=_alert_list_response([INCIDENT_SAMPLE])),
        data_source="incidents",
    )
    alert = adapter.get_alert(AlertRef(source="sangfor-xdr", external_id="incident-xyz"))
    assert alert.severity == Severity.HIGH
    assert alert.occurred_at.year == 2023
    assert "SQL注入" in alert.labels


def test_incident_evidence_aggregates_linked_alerts() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "incidents/list" in str(request.url):
            return httpx.Response(200, json=_alert_list_response([INCIDENT_SAMPLE]))
        if "/alerts/alert-linked-1/proof" in str(request.url):
            return httpx.Response(
                200,
                json=_proof_response("http", {"url": "http://x/y.jsp"}),
            )
        # incident's own proof is empty
        return httpx.Response(200, json={"code": "Success", "message": "ok", "data": {}})

    adapter = _adapter(handler, data_source="incidents")
    alert = adapter.get_alert(AlertRef(source="sangfor-xdr", external_id="incident-xyz"))
    evidence = adapter.get_evidence(alert)
    assert any(item.type == "network_flow" for item in evidence)
    assert any("/alerts/alert-linked-1/proof" in url for url in calls)


def test_severity_mapping_boundaries() -> None:
    assert map_severity(None) == Severity.LOW
    assert map_severity(10) == Severity.LOW
    assert map_severity(30) == Severity.LOW
    assert map_severity(50) == Severity.MEDIUM
    assert map_severity(70) == Severity.HIGH
    assert map_severity(100) == Severity.CRITICAL
