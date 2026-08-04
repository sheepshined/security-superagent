from app.domain.models import (
    CriticReport,
    Evidence,
    InvestigationPlan,
    InvestigationPlanStep,
    NormalizedAlert,
)


class InvestigationPlanner:
    """Create an auditable plan without granting the model execution authority."""

    def create_plan(self, alert: NormalizedAlert) -> InvestigationPlan:
        required = EvidenceCritic.required_evidence_types(alert)
        steps: list[InvestigationPlanStep] = []
        if "network_flow" in required:
            steps.append(
                InvestigationPlanStep(
                    step_id="plan_network_context",
                    agent="network_agent",
                    objective="确认通信方向、目标、频率和会话特征。",
                    required_evidence_types=["network_flow"],
                )
            )
        if "process_tree" in required:
            steps.append(
                InvestigationPlanStep(
                    step_id="plan_endpoint_context",
                    agent="endpoint_agent",
                    objective="确认发起连接的进程树、文件和终端遥测状态。",
                    required_evidence_types=["process_tree"],
                )
            )
        if "identity_event" in required:
            steps.append(
                InvestigationPlanStep(
                    step_id="plan_identity_context",
                    agent="identity_agent",
                    objective="确认操作账号、认证来源和身份事件上下文。",
                    required_evidence_types=["identity_event"],
                )
            )
        if {"asset_record", "change_record"} & required:
            steps.append(
                InvestigationPlanStep(
                    step_id="plan_business_context",
                    agent="endpoint_agent",
                    objective="核验资产归属、业务白名单和授权变更记录。",
                    required_evidence_types=sorted(
                        {"asset_record", "change_record"} & required
                    ),
                )
            )
        steps.append(
            InvestigationPlanStep(
                step_id="plan_knowledge_context",
                agent="knowledge_agent",
                objective="检索相关攻击技术、检测语义和缓解建议并保留引用。",
                required_evidence_types=[],
            )
        )
        return InvestigationPlan(
            objective=f"对“{alert.title}”形成可引用、可反证的安全研判结论。",
            steps=steps,
        )


class EvidenceCritic:
    """Verify evidence coverage and decide whether bounded evidence gathering is useful."""

    REACT_QUERYABLE_TYPES = {
        "network_flow",
        "process_tree",
        "identity_event",
        "asset_record",
        "change_record",
    }

    @classmethod
    def review(
        cls,
        *,
        alert: NormalizedAlert,
        evidence: list[Evidence],
        replan_count: int,
        max_replans: int,
    ) -> CriticReport:
        required = cls.required_evidence_types(alert)
        observed = {item.type for item in evidence}
        missing = sorted(required - observed)
        conflicts = cls._find_conflicts(observed)
        sufficient = not missing and not conflicts
        can_query = bool(set(missing) & cls.REACT_QUERYABLE_TYPES)
        requires_react = sufficient is False and can_query and replan_count < max_replans
        if sufficient:
            rationale = "关键证据类型齐全，且未发现需要阻断裁决的证据冲突。"
        elif requires_react:
            rationale = (
                f"仍缺少 {', '.join(missing)}，允许进入只读、有界 ReAct 补证子图。"
            )
        else:
            rationale = "证据仍不足或存在冲突，自动补证预算已耗尽，应保留未知项并人工确认。"
        return CriticReport(
            evidence_sufficient=sufficient,
            requires_react=requires_react,
            missing_evidence_types=missing,
            conflicts=conflicts,
            rationale=rationale,
        )

    @staticmethod
    def required_evidence_types(alert: NormalizedAlert) -> set[str]:
        labels = set(alert.labels)
        if "candidate-false-positive" in labels:
            return {"asset_record", "change_record"}
        if "lateral-movement" in labels:
            return {"network_flow", "process_tree", "identity_event"}
        if "missing-endpoint-evidence" in labels:
            return {"network_flow", "process_tree", "identity_event"}
        if "outbound" in labels:
            return {"network_flow", "process_tree"}
        return {"network_flow"}

    @staticmethod
    def _find_conflicts(observed: set[str]) -> list[str]:
        del observed
        return []
