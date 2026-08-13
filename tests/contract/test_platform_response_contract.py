"""Contract tests for PlatformResponseAdapter.

Locks the four-phase response contract the ActionService depends on
(``passed`` / ``status == "succeeded"`` / ``verified`` / ``rolled_back``)
and the fail-closed behaviour for tools the platform does not expose.
"""

import json

import httpx
import pytest
from app.adapters.platform_response import PlatformResponseAdapter
from app.domain.enums import ToolRisk, UserRole
from app.domain.models import ToolDescriptor

BASE_URL = "https://xdr.test.local"

BLOCK_DESCRIPTOR = ToolDescriptor(
    name="network.block_indicator",
    description="Bounded network indicator block.",
    input_schema={"type": "object"},
    risk_level=ToolRisk.HIGH,
    allowed_roles=[UserRole.ADMIN, UserRole.APPROVER],
    timeout_seconds=30,
    side_effects=True,
    requires_approval=True,
    idempotency_strategy="plan_hash",
)

ISOLATE_DESCRIPTOR = ToolDescriptor(
    name="edr.isolate_host",
    description="Host isolation.",
    input_schema={"type": "object"},
    risk_level=ToolRisk.HIGH,
    allowed_roles=[UserRole.ADMIN, UserRole.APPROVER],
    timeout_seconds=30,
    side_effects=True,
    requires_approval=True,
    idempotency_strategy="plan_hash",
)


def _adapter(handler) -> PlatformResponseAdapter:
    return PlatformResponseAdapter(
        base_url=BASE_URL,
        access_key="AK",
        secret_key="SK",
        transport=httpx.MockTransport(handler),
    )


def _plan(target: str, **scope) -> dict:
    return {
        "job_id": "job-1",
        "tool_name": "network.block_indicator",
        "target": target,
        "scope": scope,
        "reason": "AI 研判后的动作",
        "expected_effect": "阻断扩散",
        "rollback": "解除封禁",
        "risk_level": "high",
    }


def test_dry_run_passes_for_concrete_ip_target() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, json={}))
    outcome = adapter.dry_run(BLOCK_DESCRIPTOR, _plan("192.168.1.10"))
    assert outcome["passed"] is True
    assert outcome["mode"] == "platform"
    assert outcome["rollback_supported"] is True


def test_dry_run_rejects_unknown_target() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, json={}))
    outcome = adapter.dry_run(BLOCK_DESCRIPTOR, _plan("unknown-host"))
    assert outcome["passed"] is False
    assert outcome["conflicts"]


def test_dry_run_rejects_unsupported_tool() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, json={}))
    outcome = adapter.dry_run(ISOLATE_DESCRIPTOR, _plan("192.168.1.10"))
    assert outcome["passed"] is False
    assert any("no platform response endpoint" in item for item in outcome["conflicts"])


def test_execute_block_posts_rule_and_returns_succeeded() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200, json={"code": "Success", "message": "ok", "data": {"ids": ["rule-42"]}}
        )

    adapter = _adapter(handler)
    outcome = adapter.execute(
        BLOCK_DESCRIPTOR,
        _plan("192.168.1.10", devices=[{"devId": "af", "devType": "AF"}]),
    )
    assert outcome["status"] == "succeeded"
    assert outcome["rule_id"] == "rule-42"
    assert outcome["operation_id"] == "block_rule-42"
    assert captured["method"] == "POST"
    assert "/api/xdr/v1/responses/blockiprule/network" in captured["url"]
    # Verified platform convention: match value is a list, mode is "in".
    assert captured["json"]["blockIpRule"] == {
        "type": "SRC_IP",
        "mode": "in",
        "view": ["192.168.1.10"],
    }
    assert captured["json"]["devices"] == [{"devId": "af", "devType": "AF"}]


def test_execute_block_requires_devices() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, json={}))
    outcome = adapter.execute(BLOCK_DESCRIPTOR, _plan("192.168.1.10"))
    assert outcome["status"] == "failed"
    assert "devices" in outcome["error"]


def test_execute_unsupported_tool_fails_closed() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, json={}))
    outcome = adapter.execute(ISOLATE_DESCRIPTOR, _plan("192.168.1.10"))
    assert outcome["status"] == "failed"
    assert "not_supported" in outcome["error"]


def test_verify_checks_rule_status() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "code": "Success",
                "message": "ok",
                "data": {"item": [{"id": "rule-42", "status": "block success"}]},
            },
        )

    adapter = _adapter(handler)
    outcome = adapter.verify(
        BLOCK_DESCRIPTOR,
        _plan("192.168.1.10"),
        {"rule_id": "rule-42", "operation_id": "block_rule-42"},
    )
    assert outcome["verified"] is True
    assert outcome["observed_status"] == "block success"
    assert captured["method"] == "POST"
    assert "/api/xdr/v1/responses/blockiprule/detail" in captured["url"]
    assert captured["json"] == {"ids": ["rule-42"]}


def test_verify_missing_rule_id_returns_not_verified() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, json={}))
    outcome = adapter.verify(BLOCK_DESCRIPTOR, _plan("192.168.1.10"), {})
    assert outcome["verified"] is False
    assert "missing rule_id" in outcome["error"]


def test_rollback_unblocks_rule() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200, json={"code": "Success", "message": "ok", "data": {"success": 1}}
        )

    adapter = _adapter(handler)
    outcome = adapter.rollback(
        BLOCK_DESCRIPTOR,
        _plan("192.168.1.10"),
        {"rule_id": "rule-42", "operation_id": "block_rule-42"},
    )
    assert outcome["rolled_back"] is True
    assert outcome["success_count"] == 1
    assert captured["method"] == "POST"
    assert "/api/xdr/v1/responses/blockiprule/unblock" in captured["url"]
    assert captured["json"] == {"ids": ["rule-42"]}


def test_rollback_already_gone_rule_confirms_via_detail() -> None:
    """Real platform contract: unblock answers Success with success=0 and
    errorMessage when the record is already gone; detail then returns no
    item, so the rule is effectively rolled back."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/unblock" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": "Success",
                    "message": "成功",
                    "data": {
                        "success": 0,
                        "fail": 0,
                        "errorMessage": "封禁记录不存在",
                        "successIds": None,
                        "failedIds": None,
                    },
                },
            )
        if "/detail" in str(request.url):
            return httpx.Response(
                200, json={"code": "Success", "message": "OK", "data": {"item": []}}
            )
        raise AssertionError(f"unexpected request: {request.url}")

    adapter = _adapter(handler)
    outcome = adapter.rollback(
        BLOCK_DESCRIPTOR,
        _plan("192.168.1.10"),
        {"rule_id": "rule-42", "operation_id": "block_rule-42"},
    )
    assert outcome["rolled_back"] is True
    assert outcome["success_count"] == 0


def test_rollback_still_active_rule_is_not_reported_as_rolled_back() -> None:
    """success=0 with a still-blocking rule in detail must stay False."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/unblock" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": "Success",
                    "message": "成功",
                    "data": {"success": 0, "errorMessage": "设备未响应"},
                },
            )
        if "/detail" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": "Success",
                    "message": "OK",
                    "data": {"item": [{"id": "rule-42", "status": "block success"}]},
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    adapter = _adapter(handler)
    outcome = adapter.rollback(
        BLOCK_DESCRIPTOR,
        _plan("192.168.1.10"),
        {"rule_id": "rule-42", "operation_id": "block_rule-42"},
    )
    assert outcome["rolled_back"] is False


def test_rollback_missing_rule_id_reports_failure() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, json={}))
    outcome = adapter.rollback(BLOCK_DESCRIPTOR, _plan("192.168.1.10"), {})
    assert outcome["rolled_back"] is False


def test_block_type_detection() -> None:
    assert PlatformResponseAdapter._block_type("8.8.8.8") == "SRC_IP"
    assert PlatformResponseAdapter._block_type("evil.example.com") == "DNS"
    assert PlatformResponseAdapter._block_type("https://evil.example.com/x") == "URL"


def test_platform_error_code_raises() -> None:
    from app.adapters.platform_xdr import XdrApiError

    adapter = _adapter(
        lambda request: httpx.Response(
            200, json={"code": "Failed", "message": "no", "data": None}
        )
    )
    with pytest.raises(XdrApiError):
        adapter.execute(
            BLOCK_DESCRIPTOR,
            _plan("192.168.1.10", devices=[{"devId": "d1"}]),
        )
