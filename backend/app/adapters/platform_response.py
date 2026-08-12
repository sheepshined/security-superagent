"""Sangfor XDR platform response adapter.

Implements the ``ResponseAdapter`` protocol against the Sangfor XDR
OpenAPI.  The action service relies on the returned keys ``passed``,
``status == "succeeded"``, ``verified`` and ``rolled_back``; this adapter
keeps those contracts while mapping domain tools to platform endpoints.

Platform gap: the OpenAPI list exposes no "create host isolation" endpoint
and no account-disable endpoint, so ``edr.isolate_host`` and
``iam.disable_account`` fail closed with ``not_supported``.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.adapters.aksk import AkskSigner
from app.adapters.platform_xdr import XdrApiError
from app.adapters.xdr_schemas import XdrApiResponse
from app.domain.models import ToolDescriptor, utc_now

_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_ISOLATION_TOOL = "edr.isolate_host"
_ACCOUNT_TOOL = "iam.disable_account"
_SUPPORTED_WRITE_TOOLS = {"network.block_indicator"}


class PlatformResponseAdapter:
    """Real response execution through the Sangfor XDR OpenAPI."""

    name = "platform-response"

    def __init__(
        self,
        *,
        base_url: str,
        access_key: str | None = None,
        secret_key: str | None = None,
        auth_code: str | None = None,
        timeout_seconds: float = 30.0,
        tls_verify: bool = True,
        ca_bundle: str = "",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("XDR base URL is required")
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._verify = tls_verify
        self._ca_bundle = ca_bundle
        self._transport = transport
        self._signer = AkskSigner(
            access_key=access_key,
            secret_key=secret_key,
            auth_code=auth_code,
        )

    # -- ResponseAdapter protocol ----------------------------------------------

    def dry_run(self, descriptor: ToolDescriptor, plan: dict[str, Any]) -> dict[str, Any]:
        target = str(plan.get("target", "")).strip()
        conflicts: list[str] = []
        if descriptor.name not in _SUPPORTED_WRITE_TOOLS:
            conflicts.append(f"tool {descriptor.name} has no platform response endpoint")
        if not target or target == "unknown-host":
            conflicts.append("target is not a concrete normalized entity")
        if descriptor.name == "network.block_indicator" and not self._block_type(target):
            conflicts.append("target is neither an IP, a domain, nor a URL")
        return {
            "adapter": self.name,
            "mode": "platform",
            "passed": not conflicts,
            "target": target,
            "tool_name": descriptor.name,
            "conflicts": conflicts,
            "rollback_supported": True,
            "checked_at": utc_now().isoformat(),
        }

    def execute(self, descriptor: ToolDescriptor, plan: dict[str, Any]) -> dict[str, Any]:
        target = str(plan.get("target", "")).strip()
        if descriptor.name in {_ISOLATION_TOOL, _ACCOUNT_TOOL}:
            return self._not_supported(descriptor.name, target)
        if descriptor.name == "network.block_indicator":
            return self._execute_block(plan, target)
        return self._not_supported(descriptor.name, target)

    def verify(
        self,
        descriptor: ToolDescriptor,
        plan: dict[str, Any],
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        rule_id = execution.get("rule_id")
        if not rule_id:
            return {
                "adapter": self.name,
                "mode": "platform",
                "verified": False,
                "operation_id": execution.get("operation_id"),
                "error": "missing rule_id from execution",
                "verified_at": utc_now().isoformat(),
            }
        body = self._signed_request(
            "GET",
            f"{self._base_url}/api/v1.0/openapi/responses/blockiprule/detail",
            params={"ids": json.dumps([rule_id])},
        )
        response = XdrApiResponse.model_validate(body)
        if response.code and response.code != "Success":
            raise XdrApiError(
                f"platform returned code={response.code}: {response.message}"
            )
        status = self._first_status(response)
        verified = status == "block success"
        return {
            "adapter": self.name,
            "mode": "platform",
            "verified": verified,
            "operation_id": execution.get("operation_id"),
            "rule_id": rule_id,
            "observed_status": status,
            "verified_at": utc_now().isoformat(),
        }

    def rollback(
        self,
        descriptor: ToolDescriptor,
        plan: dict[str, Any],
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        rule_id = execution.get("rule_id")
        if not rule_id:
            return {
                "adapter": self.name,
                "mode": "platform",
                "rolled_back": False,
                "error": "missing rule_id from execution",
                "rolled_back_at": utc_now().isoformat(),
            }
        body = self._signed_request(
            "GET",
            f"{self._base_url}/api/v1.0/openapi/responses/blockiprule/unblock",
            params={"ids": json.dumps([rule_id])},
        )
        response = XdrApiResponse.model_validate(body)
        data = response.data if isinstance(response.data, dict) else {}
        success = int(data.get("success", 0) or 0)
        return {
            "adapter": self.name,
            "mode": "platform",
            "rolled_back": success >= 1,
            "operation_id": execution.get("operation_id"),
            "rule_id": rule_id,
            "success_count": success,
            "rolled_back_at": utc_now().isoformat(),
        }

    # -- platform write helpers -------------------------------------------------

    def _execute_block(self, plan: dict[str, Any], target: str) -> dict[str, Any]:
        scope = plan.get("scope") or {}
        devices = scope.get("devices")
        if not devices:
            return {
                "adapter": self.name,
                "mode": "platform",
                "status": "failed",
                "error": "block requires scope.devices to select connected devices",
                "tool_name": "network.block_indicator",
                "target": target,
                "executed_at": utc_now().isoformat(),
            }
        block_type = self._block_type(target)
        body = self._signed_request(
            "POST",
            f"{self._base_url}/api/v1.0/openapi/responses/blockiprule/network",
            json_body={
                "name": scope.get("name") or f"superagent-block-{target[:32]}",
                "reason": plan.get("reason", "AI 研判后的人工审批动作"),
                "timeType": scope.get("timeType", "temporary"),
                "timeValue": scope.get("timeValue", 30),
                "timeUnit": scope.get("timeUnit", "m"),
                "blockIpRule": {"type": block_type, "view": target},
                "devices": devices,
            },
        )
        response = XdrApiResponse.model_validate(body)
        if response.code and response.code != "Success":
            raise XdrApiError(
                f"platform returned code={response.code}: {response.message}"
            )
        data = response.data if isinstance(response.data, dict) else {}
        ids = data.get("ids")
        rule_id = ids[0] if isinstance(ids, list) and ids else str(ids or "")
        if not rule_id:
            raise XdrApiError("platform block response did not include a rule id")
        return {
            "adapter": self.name,
            "mode": "platform",
            "status": "succeeded",
            "operation_id": f"block_{rule_id}",
            "rule_id": rule_id,
            "tool_name": "network.block_indicator",
            "target": target,
            "executed_at": utc_now().isoformat(),
        }

    def _not_supported(self, tool_name: str, target: str) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "mode": "platform",
            "status": "failed",
            "error": (
                f"not_supported: 平台 OpenAPI 无 {tool_name} 对应接口；"
                "已向平台方确认新增隔离/账号禁用接口，当前安全降级为失败。"
            ),
            "tool_name": tool_name,
            "target": target,
            "executed_at": utc_now().isoformat(),
        }

    @staticmethod
    def _block_type(target: str) -> str:
        if _IP_RE.match(target):
            return "SRC_IP"
        if "://" in target or target.startswith("/"):
            return "URL"
        if "." in target:
            return "DNS"
        return "SRC_IP"

    @staticmethod
    def _first_status(response: XdrApiResponse) -> str | None:
        if isinstance(response.data, dict):
            items = response.data.get("item")
            if isinstance(items, list) and items:
                status = items[0].get("status") if isinstance(items[0], dict) else None
                if status:
                    return str(status)
        return None

    def _signed_request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> dict[str, Any]:
        headers = self._signer.sign(
            method=method,
            url=url,
            headers={"content-type": "application/json"},
            params=params or {},
            json_body=json_body,
        )
        client_kwargs: dict[str, Any] = {
            "timeout": self._timeout_seconds,
            "verify": self._ca_bundle if self._ca_bundle else self._verify,
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        with httpx.Client(**client_kwargs) as client:
            response = client.request(
                method,
                url,
                headers=headers,
                params=params or None,
                json=json_body,
            )
            response.raise_for_status()
            return response.json()
