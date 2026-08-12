"""Sangfor XDR platform adapter.

Implements the ``XDRAdapter`` protocol against the Sangfor XDR OpenAPI.
All credentials come from settings and are never logged; every request is
signed with the AKSK HMAC-SHA256 scheme.

Known platform gap: the OpenAPI list has no "create host isolation" endpoint,
so the response adapter reports ``edr.isolate_host`` as not supported.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.adapters.aksk import AkskSigner
from app.adapters.xdr_schemas import (
    AlertItem,
    IncidentItem,
    ProofData,
    XdrApiResponse,
    to_evidence,
    to_normalized_alert,
    to_scenario_summary,
)
from app.domain.models import (
    AlertRef,
    Evidence,
    NormalizedAlert,
    ScenarioSummary,
)

ALERT_SOURCE = "sangfor-xdr"
# Verified against the real XDR 2.0.20 platform: the AKSK-signed gateway
# lives under /api/xdr/v1. The eolink document marks alerts/list as GET and
# proof as POST, but the platform actually accepts alerts/list as POST+body
# and proof as GET; the adapter follows the verified calling convention.
API_PREFIX = "/api/xdr/v1"


class XdrApiError(RuntimeError):
    """A platform request failed; ``retryable`` drives degradation handling."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class XdrAlertNotFoundError(LookupError):
    pass


class PlatformXDRAdapter:
    """Read-only data access to a Sangfor XDR platform instance."""

    def __init__(
        self,
        *,
        base_url: str,
        auth_code: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        data_source: str = "alerts",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        default_page_size: int = 50,
        tls_verify: bool = True,
        ca_bundle: str = "",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("XDR base URL is required")
        if data_source not in {"alerts", "incidents"}:
            raise ValueError("XDR data source must be 'alerts' or 'incidents'")
        self._base_url = base_url.rstrip("/")
        self._data_source = data_source
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(1, min(max_retries, 3))
        self._default_page_size = max(5, min(default_page_size, 200))
        self._verify = tls_verify
        self._ca_bundle = ca_bundle
        self._transport = transport
        self._signer = AkskSigner(
            auth_code=auth_code,
            access_key=access_key,
            secret_key=secret_key,
        )

    @property
    def data_source(self) -> str:
        return self._data_source

    # -- XDRAdapter protocol -------------------------------------------------

    def list_scenarios(self) -> list[ScenarioSummary]:
        payload = self._list_alerts(page=1, page_size=self._default_page_size)
        return [to_scenario_summary(item, ALERT_SOURCE) for item in payload]

    def get_alert(self, alert_ref: AlertRef) -> NormalizedAlert:
        payload = self._list_alerts(page=1, page_size=5, uu_ids=[alert_ref.external_id])
        if not payload:
            raise XdrAlertNotFoundError(
                f"platform {self._data_source} not found: {alert_ref.external_id}"
            )
        item = payload[0]
        alert = to_normalized_alert(
            item,
            source=alert_ref.source if alert_ref.source else ALERT_SOURCE,
            external_prefix="alert" if self._data_source == "alerts" else "incident",
        )
        if alert.external_id != alert_ref.external_id:
            raise XdrAlertNotFoundError(
                f"platform alert mismatch: {alert_ref.external_id}"
            )
        return alert

    def get_evidence(self, alert: NormalizedAlert) -> list[Evidence]:
        proof = self._fetch_proof(alert.external_id)
        return to_evidence(proof, ALERT_SOURCE, alert)

    # -- platform calls ------------------------------------------------------

    def _list_alerts(
        self,
        *,
        page: int,
        page_size: int,
        uu_ids: list[str] | None = None,
    ) -> list[AlertItem]:
        body_payload: dict[str, Any] = {"page": page, "pageSize": page_size}
        if uu_ids:
            body_payload["uuIds"] = uu_ids
        body = self._signed_request(
            "POST",
            f"{self._base_url}{API_PREFIX}/{self._data_source}/list",
            json_body=body_payload,
        )
        response = XdrApiResponse.model_validate(body)
        self._ensure_success(response)
        items = self._extract_items(response)
        model = IncidentItem if self._data_source == "incidents" else AlertItem
        return [model.model_validate(item) for item in items]

    def _fetch_proof(self, external_id: str) -> ProofData:
        body = self._signed_request(
            "GET",
            f"{self._base_url}{API_PREFIX}/{self._data_source}/{external_id}/proof",
        )
        response = XdrApiResponse.model_validate(body)
        self._ensure_success(response)
        data = response.data if isinstance(response.data, dict) else {}
        return ProofData.model_validate(data)

    @staticmethod
    def _extract_items(response: XdrApiResponse) -> list[dict[str, Any]]:
        if isinstance(response.data, list):
            return [item for item in response.data if isinstance(item, dict)]
        if isinstance(response.data, dict):
            items = response.data.get("item")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    @staticmethod
    def _ensure_success(response: XdrApiResponse) -> None:
        if response.code and response.code != "Success":
            raise XdrApiError(
                f"platform returned code={response.code}: {response.message}",
                retryable=False,
            )

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
        last_error: Exception | None = None
        attempts = self._max_retries + 1
        client_kwargs: dict[str, Any] = {
            "timeout": self._timeout_seconds,
            "verify": self._ca_bundle if self._ca_bundle else self._verify,
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        with httpx.Client(**client_kwargs) as client:
            for attempt in range(1, attempts + 1):
                try:
                    response = client.request(
                        method,
                        url,
                        headers=headers,
                        params=params or None,
                        json=json_body,
                    )
                    if response.status_code in {429, 500, 502, 503, 504} and attempt < attempts:
                        last_error = XdrApiError(
                            f"platform status {response.status_code}",
                            retryable=True,
                        )
                        time.sleep(0.25 * attempt)
                        continue
                    response.raise_for_status()
                    return response.json()
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = XdrApiError(str(exc), retryable=True)
                    if attempt < attempts:
                        time.sleep(0.25 * attempt)
                        continue
                    raise XdrApiError(
                        f"platform unreachable: {exc}", retryable=True
                    ) from exc
        if last_error is not None:
            raise last_error
        raise XdrApiError("platform request failed without a response", retryable=True)
