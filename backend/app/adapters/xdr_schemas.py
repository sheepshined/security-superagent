"""Sangfor XDR OpenAPI response schemas and deterministic field mapping.

Platform responses are flattened with many protocol-specific fields; the
mapping here keeps only the fields the domain model needs and ignores the
rest.  All timestamps are Unix seconds converted to UTC datetimes, matching
the project-wide UTC convention.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Severity, Verdict
from app.domain.models import (
    AlertRef,
    Entity,
    Evidence,
    Indicator,
    NormalizedAlert,
    ScenarioSummary,
)

ACCESS_LABEL_PLATFORM = "platform"


class XdrApiResponse(BaseModel):
    """Common {code, message, data} envelope used by every platform endpoint."""

    model_config = ConfigDict(extra="ignore")

    code: str = ""
    message: str = ""
    data: dict[str, Any] | list[Any] | None = None


class AlertItem(BaseModel):
    """Subset of the alerts/list item fields consumed by the domain model."""

    model_config = ConfigDict(extra="ignore")

    uuId: str | None = None
    name: str | None = None
    firstTime: int | None = None
    lastTime: int | None = None
    severity: int | None = None
    srcIp: str | list[str] | None = None
    dstIp: str | list[str] | None = None
    hostIp: str | None = None
    srcPort: int | str | list[int | str] | None = None
    dstPort: int | str | list[int | str] | None = None
    url: str | list[str] | None = None
    domain: str | list[str] | None = None
    fileMd5: str | list[str] | None = None
    exploitCveId: str | list[str] | None = None
    threatClassDesc: str | None = None
    threatTypeDesc: str | None = None
    threatSubTypeDesc: str | None = None
    attackState: int | None = None
    threatDefine: list[int] | int | None = None
    description: str | None = None


class IncidentItem(AlertItem):
    """Incidents/list item fields.

    Verified against XDR 2.0.20: incidents use ``incidentSeverity`` (0-4
    enum), ``startTime``/``endTime`` timestamps, and carry ``alertIds``
    linking the aggregated alerts whose proofs form the incident evidence.
    """

    model_config = ConfigDict(extra="ignore")

    incidentSeverity: int | None = None
    alertIds: list[str] | None = None
    riskTag: list[str] | None = None
    startTime: int | None = None
    endTime: int | None = None


class ProofData(BaseModel):
    """Top-level fields of the alerts/:uuid/proof response data object."""

    model_config = ConfigDict(extra="ignore")

    uuId: str | None = None
    name: str | None = None
    severity: int | None = None
    lastTime: int | None = None
    srcIp: str | list[str] | None = None
    dstIp: str | list[str] | None = None
    proofType: str | list[str] | None = None
    proof: dict[str, Any] = Field(default_factory=dict)
    originalAlert: dict[str, Any] | None = None
    gptResult: int | None = None
    gptResultDescription: str | None = None


def as_list(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [value] if str(value).strip() else []


def map_severity(value: int | None) -> Severity:
    """Map platform alert 0-100 severity to the domain enum.

    Platform buckets: (0,10] info, (10,30] low, (30,50] medium,
    (50,70] high, (70,100] critical.
    """
    if value is None or value <= 30:
        return Severity.LOW
    if value <= 50:
        return Severity.MEDIUM
    if value <= 70:
        return Severity.HIGH
    return Severity.CRITICAL


def map_incident_severity(value: int | None) -> Severity:
    """Map platform incident 0-4 severity enum to the domain enum.

    Platform buckets: 0 info, 1 low, 2 medium, 3 high, 4 critical.
    """
    if value is None or value <= 1:
        return Severity.LOW
    if value == 2:
        return Severity.MEDIUM
    if value == 3:
        return Severity.HIGH
    return Severity.CRITICAL


def ts_to_dt(value: int | None) -> datetime:
    """Convert a Unix-seconds timestamp to an aware UTC datetime."""
    if value is None:
        return datetime.now(UTC)
    # Tolerate millisecond timestamps in case a platform version emits them.
    if value > 10**12:
        value = value // 1000
    return datetime.fromtimestamp(value, tz=UTC)


def _entities(item: AlertItem) -> list[Entity]:
    entities: list[Entity] = []
    for ip in as_list(item.srcIp):
        entities.append(Entity(type="ip", id=ip, role="source"))
    for ip in as_list(item.dstIp):
        entities.append(Entity(type="ip", id=ip, role="target"))
    if item.hostIp:
        entities.append(Entity(type="host", id=item.hostIp, role="asset"))
    return entities


def _indicators(item: AlertItem) -> list[Indicator]:
    indicators: list[Indicator] = []
    for md5 in as_list(item.fileMd5):
        indicators.append(Indicator(type="hash", value=md5))
    for domain in as_list(item.domain):
        indicators.append(Indicator(type="domain", value=domain))
    for url in as_list(item.url):
        indicators.append(Indicator(type="url", value=url))
    for cve in as_list(item.exploitCveId):
        indicators.append(Indicator(type="cve", value=cve))
    return indicators


def _labels(item: AlertItem) -> list[str]:
    labels: list[str] = []
    for field in (
        item.threatClassDesc,
        item.threatTypeDesc,
        item.threatSubTypeDesc,
    ):
        if field and field not in labels:
            labels.append(field)
    for tag in getattr(item, "riskTag", None) or []:
        if tag and tag not in labels:
            labels.append(tag)
    if item.attackState is not None:
        labels.append(f"attack_state_{item.attackState}")
    threat_defines = (
        list(item.threatDefine) if isinstance(item.threatDefine, list) else [item.threatDefine]
    )
    for value in threat_defines:
        if value is not None:
            labels.append(f"threat_define_{value}")
    return labels


def to_normalized_alert(
    item: AlertItem,
    source: str,
    external_prefix: str = "alert",
) -> NormalizedAlert:
    """Map a platform alert/incident item to the domain NormalizedAlert."""
    external_id = item.uuId or f"{external_prefix}_unknown"
    is_incident = isinstance(item, IncidentItem)
    if is_incident and item.startTime is not None:
        occurred = ts_to_dt(item.startTime)
    else:
        occurred = ts_to_dt(item.lastTime or item.firstTime)
    if is_incident:
        severity = map_incident_severity(item.incidentSeverity)
    else:
        severity = map_severity(item.severity)
    return NormalizedAlert(
        alert_id=f"xdr_{external_prefix}_{external_id}",
        source=source,
        external_id=external_id,
        title=item.name or "未命名告警",
        severity=severity,
        occurred_at=occurred,
        entities=_entities(item),
        indicators=_indicators(item),
        labels=_labels(item),
        raw_ref=f"xdr://{source}/{external_prefix}/{external_id}",
    )


def to_scenario_summary(item: AlertItem, source: str) -> ScenarioSummary:
    """Map a platform alert/incident item to a ScenarioSummary for the console."""
    return ScenarioSummary(
        scenario_id=item.uuId or "unknown",
        title=item.name or "未命名告警",
        description=(item.description or "来自深信服 XDR 平台的告警/事件。")[:2000],
        severity=map_severity(item.severity),
        expected_verdict=Verdict.NEEDS_REVIEW,
        supports_superagent=False,
    )


# Proof-type buckets aligned with the read-only investigation tools allowlist.
_NETWORK_PROOF_TYPES = {
    "http",
    "dns",
    "tcp",
    "smb",
    "rdp",
    "ssh",
    "ldap",
    "dcerpc",
    "socks",
    "redis command",
    "redis login",
    "mongo",
    "mysql login",
    "mysql command",
    "sqlserver",
    "pop3",
    "smtp",
    "ftp",
    "telnet command",
    "telnet option",
    "icmp",
    "oracle",
    "ssl",
    "snmp",
    "ntlm",
    "db2",
    "sas",
    "vpn",
    "exploit",
    "packet",
    "webmail",
    "email",
    "threati",
    "networkInfo",
}
_ENDPOINT_PROOF_TYPES = {"file", "virus", "webshell", "scan", "rasp", "raw"}
_IDENTITY_PROOF_TYPES = {
    "account",
    "bruteForce",
    "login",
    "kerberos",
    "proof list",
}


def _evidence_type_for(proof_type: str) -> str:
    if proof_type in _NETWORK_PROOF_TYPES:
        return "network_flow"
    if proof_type in _ENDPOINT_PROOF_TYPES:
        return "process_tree"
    if proof_type in _IDENTITY_PROOF_TYPES:
        return "identity_event"
    return "telemetry_gap"


def to_evidence(proof: ProofData, source: str, alert: NormalizedAlert) -> list[Evidence]:
    """Map platform proof payloads to domain Evidence items.

    The platform returns ``proof`` as a flat field dictionary plus a
    ``proofType`` list; each declared proof type becomes one Evidence item
    sharing the flat fields.  Unclassified proof types become
    ``telemetry_gap`` entries so the Critic can report missing evidence
    instead of silently ignoring it.
    """
    observed_at = ts_to_dt(proof.lastTime)
    items: list[Evidence] = []
    flat_fields = dict(proof.proof)
    for raw_type in as_list(proof.proofType):
        proof_type = raw_type.strip()
        if not proof_type:
            continue
        statement = _proof_statement(proof_type, flat_fields)
        evidence_type = _evidence_type_for(proof_type)
        items.append(
            Evidence(
                evidence_id=f"ev_{proof_type}_{alert.external_id[:16]}",
                type=evidence_type,
                source=f"{source}-proof",
                statement=statement,
                observed_at=observed_at,
                confidence=_proof_confidence(flat_fields, proof_type),
                raw_ref=f"xdr://{source}/proof/{alert.external_id}/{proof_type}",
                integrity_hash=_hash_proof(flat_fields),
                access_label=ACCESS_LABEL_PLATFORM,
            )
        )
    if not items:
        items.append(
            Evidence(
                evidence_id=f"ev_gap_{alert.external_id[:24]}",
                type="telemetry_gap",
                source=f"{source}-proof",
                statement=f"平台未返回 {alert.title} 的可归类举证（proofType 为空或缺失）。",
                observed_at=observed_at,
                confidence=0.0,
                raw_ref=f"xdr://{source}/proof/{alert.external_id}",
                integrity_hash=_hash_proof(flat_fields),
                access_label=ACCESS_LABEL_PLATFORM,
            )
        )
    return items


def _proof_statement(proof_type: str, fields: dict[str, Any]) -> str:
    summary_fields = {
        "srcIp",
        "dstIp",
        "srcPort",
        "dstPort",
        "url",
        "domain",
        "fileName",
        "filePath",
        "userName",
        "virusName",
        "ftpCommand",
        "smtpSubject",
        "loginUser",
        "exploitCveId",
    }
    present = {key: value for key, value in fields.items() if key in summary_fields and value}
    summary = "，".join(f"{key}={value}" for key, value in present.items())
    base = f"{proof_type} 类型举证"
    return f"{base}：{summary}。" if summary else f"{base}（无摘要字段）。"


def _proof_confidence(fields: dict[str, Any], proof_type: str) -> float:
    # Network protocol proofs carry packet-level fields; endpoint and identity
    # proofs carry explicit signals.  Deterministic estimate only.
    if not fields:
        return 0.5
    if proof_type in {"virus", "exploit", "account", "bruteForce", "webshell"}:
        return 0.8
    return 0.7


def _hash_proof(fields: dict[str, Any]) -> str:
    canonical = json.dumps(fields, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def make_alert_ref(source: str, external_id: str) -> AlertRef:
    return AlertRef(source=source, external_id=external_id)
