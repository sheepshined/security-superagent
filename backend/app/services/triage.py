from uuid import uuid4

from app.adapters.protocols import XDRAdapter
from app.domain.enums import FactType, ToolRisk, Verdict
from app.domain.models import (
    ActionProposal,
    AlertRef,
    Citation,
    Evidence,
    Finding,
    InvestigationTrace,
    NormalizedAlert,
    TriageResult,
)
from app.rag.hybrid import HybridRetriever
from app.services.risk import calculate_risk


class TriageService:
    """Evidence-first deterministic baseline used before the LLM is introduced."""

    def __init__(self, xdr: XDRAdapter, retriever: HybridRetriever | None = None) -> None:
        self._xdr = xdr
        self._retriever = retriever

    def run(self, *, job_id: str, case_id: str, alert_ref: AlertRef) -> TriageResult:
        alert, evidence = self.fetch_context(alert_ref)
        citations = self.retrieve_citations(alert.title, alert.labels, evidence)
        return self.build_result(
            job_id=job_id,
            case_id=case_id,
            alert=alert,
            evidence=evidence,
            citations=citations,
            workflow_trace=["deterministic_baseline"],
        )

    def fetch_context(self, alert_ref: AlertRef) -> tuple[NormalizedAlert, list[Evidence]]:
        alert = self._xdr.get_alert(alert_ref)
        return alert, self._xdr.get_evidence(alert)

    def build_result(
        self,
        *,
        job_id: str,
        case_id: str,
        alert: NormalizedAlert,
        evidence: list[Evidence],
        citations: list[Citation] | None = None,
        workflow_trace: list[str] | None = None,
        model_assessment: dict | None = None,
        investigation: InvestigationTrace | None = None,
    ) -> TriageResult:
        labels = set(alert.labels)
        evidence_types = {item.type for item in evidence}

        verdict = self._decide_verdict(labels, evidence_types)
        risk_score = calculate_risk(alert, evidence)
        confidence = self._confidence(verdict, evidence)
        if verdict == Verdict.FALSE_POSITIVE:
            risk_score = min(risk_score, 25)
        elif verdict == Verdict.NEEDS_REVIEW:
            risk_score = min(risk_score, 64)

        findings = [
            Finding(
                finding_id=f"finding_{uuid4().hex[:12]}",
                statement=item.statement,
                fact_type=FactType.OBSERVED_FACT,
                evidence_ids=[item.evidence_id],
                confidence=item.confidence,
            )
            for item in evidence
        ]
        unknowns = self._unknowns(evidence_types)
        actions = self._actions(verdict, alert.entities)
        assessment = self._validate_model_assessment(model_assessment, verdict, evidence)
        return TriageResult(
            job_id=job_id,
            case_id=case_id,
            verdict=verdict,
            risk_score=risk_score,
            confidence=confidence,
            summary=self._summary(verdict, alert.title, risk_score),
            facts=findings,
            unknowns=unknowns,
            evidence=evidence,
            citations=citations or [],
            recommended_actions=actions,
            requires_superagent="lateral-movement" in labels and verdict == Verdict.TRUE_POSITIVE,
            workflow_trace=workflow_trace or [],
            model_assessment=assessment,
            investigation=investigation,
        )

    def retrieve_citations(
        self,
        title: str,
        labels: list[str],
        evidence: list[Evidence],
    ) -> list[Citation]:
        if not self._retriever:
            return []
        query = " ".join([title, *labels, *(item.statement for item in evidence)])
        hits = self._retriever.search(
            query,
            top_k=4,
            allowed_access_labels={"public", "synthetic"},
        )
        return [
            Citation(
                citation_id=f"citation_{hit.chunk.chunk_id.replace(':', '_')}",
                document_id=hit.chunk.document_id,
                chunk_id=hit.chunk.chunk_id,
                title=hit.chunk.title,
                source_uri=hit.chunk.source_uri,
                version=hit.chunk.version,
                excerpt=hit.chunk.content,
            )
            for hit in hits
            if hit.final_score >= 0.15
        ]

    @staticmethod
    def _validate_model_assessment(
        assessment: dict | None,
        policy_verdict: Verdict,
        evidence: list[Evidence],
    ) -> dict | None:
        if not assessment:
            return None
        known_evidence_ids = {item.evidence_id for item in evidence}
        supporting_ids = set(assessment.get("supporting_evidence_ids", []))
        validated = dict(assessment)
        validated["agrees_with_policy"] = assessment.get("verdict") == policy_verdict.value
        validated["evidence_grounded"] = supporting_ids <= known_evidence_ids
        validated["decision_authority"] = "deterministic_policy"
        return validated

    # Platform threat-definition labels (from Sangfor threatDefine/attackState).
    _PLATFORM_REAL_THREAT_LABELS = {"threat_define_500", "threat_define_900"}
    _PLATFORM_ATTACK_SUCCEEDED_LABELS = {"attack_state_2", "attack_state_3"}

    @staticmethod
    def _decide_verdict(labels: set[str], evidence_types: set[str]) -> Verdict:
        if (
            "candidate-false-positive" in labels
            and {"asset_record", "change_record"} <= evidence_types
        ):
            return Verdict.FALSE_POSITIVE
        if "missing-endpoint-evidence" in labels or "telemetry_gap" in evidence_types:
            return Verdict.NEEDS_REVIEW
        if (
            "lateral-movement" in labels
            and {
                "network_flow",
                "process_tree",
                "identity_event",
            }
            <= evidence_types
        ):
            return Verdict.TRUE_POSITIVE
        # Moderate platform-attestation: the platform engine already classifies
        # the alert as a real threat (virus/directed attack) with a succeeded
        # attack state, and the agent gathered at least network evidence. The
        # verdict is still evidence-gated; no response action is auto-executed.
        if (
            labels & TriageService._PLATFORM_REAL_THREAT_LABELS
            and labels & TriageService._PLATFORM_ATTACK_SUCCEEDED_LABELS
            and "network_flow" in evidence_types
        ):
            return Verdict.TRUE_POSITIVE
        return Verdict.NEEDS_REVIEW

    @staticmethod
    def _confidence(verdict: Verdict, evidence: list) -> float:
        average = sum(item.confidence for item in evidence) / max(len(evidence), 1)
        if verdict == Verdict.NEEDS_REVIEW:
            return round(min(average, 0.62), 2)
        if verdict == Verdict.TRUE_POSITIVE:
            # More distinct evidence types raise the confidence moderately.
            type_count = len({item.type for item in evidence})
            boosted = average + min(0.05 * type_count, 0.15)
            return round(min(boosted, 0.95), 2)
        return round(min(average, 0.99), 2)

    @staticmethod
    def _unknowns(evidence_types: set[str]) -> list[str]:
        unknowns: list[str] = []
        if "process_tree" not in evidence_types:
            unknowns.append("缺少端点进程树，无法确认网络连接的发起进程。")
        if "identity_event" not in evidence_types:
            unknowns.append("缺少身份登录证据，无法确认操作账号及认证上下文。")
        return unknowns

    @staticmethod
    def _actions(verdict: Verdict, entities: list) -> list[ActionProposal]:
        if verdict != Verdict.TRUE_POSITIVE:
            return []
        source_host = next(
            (entity.id for entity in entities if entity.type == "host" and entity.role == "source"),
            None,
        )
        source_ip = next(
            (entity.id for entity in entities if entity.type == "ip" and entity.role == "source"),
            None,
        )
        if source_host is None and source_ip is None:
            return []
        if source_host is not None:
            return [
                ActionProposal(
                    tool_name="edr.isolate_host",
                    target=source_host,
                    scope={"ttl_minutes": 30},
                    reason="多源证据支持疑似横向移动，需要限制继续扩散。",
                    expected_effect="阻断该终端的新网络连接并保留调查通道。",
                    rollback="审批者可使用返回的 rollback_token 解除隔离。",
                    risk_level=ToolRisk.HIGH,
                    dry_run=True,
                    requires_approval=True,
                )
            ]
        return [
            ActionProposal(
                tool_name="network.block_indicator",
                target=source_ip,
                scope={"timeType": "temporary", "timeValue": 30, "timeUnit": "m"},
                reason="平台定性为真实攻击且攻击成功，需要限制攻击源继续扩散。",
                expected_effect="在联动设备上临时封禁攻击源 IP 的出向访问。",
                rollback="审批者可通过解除封禁恢复该 IP 的访问。",
                risk_level=ToolRisk.HIGH,
                dry_run=True,
                requires_approval=True,
            )
        ]

    @staticmethod
    def _summary(verdict: Verdict, title: str, risk_score: int) -> str:
        prefix = {
            Verdict.TRUE_POSITIVE: "多源证据支持真实威胁",
            Verdict.FALSE_POSITIVE: "可信反证支持业务误报",
            Verdict.NEEDS_REVIEW: "关键证据不足，结论待确认",
            Verdict.DUPLICATE: "该告警已由现有任务处理",
        }[verdict]
        return f"{prefix}：{title}。确定性风险分数为 {risk_score}。"
