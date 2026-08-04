from app.domain.models import (
    CriticReport,
    Evidence,
    InvestigationPlan,
    InvestigationPlanStep,
    NormalizedAlert,
    ReplanRequest,
)


class InvestigationPlanner:
    """Create an auditable plan without granting the model execution authority."""

    def create_plan(
        self,
        alert: NormalizedAlert,
        analyst_query: str = "",
    ) -> InvestigationPlan:
        required = EvidenceCritic.required_evidence_types(alert)
        query_context = (
            f" Analyst investigation goal: {analyst_query.strip()}"
            if analyst_query.strip()
            else ""
        )
        steps: list[InvestigationPlanStep] = []
        if "network_flow" in required:
            steps.append(
                InvestigationPlanStep(
                    step_id="plan_network_context",
                    agent="network_agent",
                    objective=(
                        "Confirm communication direction, targets, frequency, and session "
                        f"characteristics.{query_context}"
                    ),
                    required_evidence_types=["network_flow"],
                )
            )
        if "process_tree" in required:
            steps.append(
                InvestigationPlanStep(
                    step_id="plan_endpoint_context",
                    agent="endpoint_agent",
                    objective=(
                        "Confirm the initiating process tree, files, and endpoint telemetry "
                        f"state.{query_context}"
                    ),
                    required_evidence_types=["process_tree"],
                )
            )
        if "identity_event" in required:
            steps.append(
                InvestigationPlanStep(
                    step_id="plan_identity_context",
                    agent="identity_agent",
                    objective=(
                        "Confirm the operating account, authentication source, and identity "
                        f"event context.{query_context}"
                    ),
                    required_evidence_types=["identity_event"],
                )
            )
        if {"asset_record", "change_record"} & required:
            steps.append(
                InvestigationPlanStep(
                    step_id="plan_business_context",
                    agent="endpoint_agent",
                    objective=(
                        "Verify asset ownership, business allowlists, and authorized change "
                        f"records.{query_context}"
                    ),
                    required_evidence_types=sorted(
                        {"asset_record", "change_record"} & required
                    ),
                )
            )
        steps.append(
            InvestigationPlanStep(
                step_id="plan_knowledge_context",
                agent="knowledge_agent",
                objective=(
                    "Retrieve relevant attack techniques, detection semantics, mitigations, "
                    f"and traceable sources.{query_context}"
                ),
                required_evidence_types=[],
            )
        )
        return InvestigationPlan(
            objective=(
                f"Form a citable and falsifiable security assessment for '{alert.title}'."
                f"{query_context}"
            ),
            steps=steps,
        )

    def create_replan(
        self,
        *,
        plan: InvestigationPlan,
        critic: CriticReport,
        replan_number: int,
    ) -> ReplanRequest:
        agent_by_type = {
            "network_flow": "network_agent",
            "process_tree": "endpoint_agent",
            "identity_event": "identity_agent",
            "asset_record": "endpoint_agent",
            "change_record": "endpoint_agent",
        }
        revised_steps = [
            InvestigationPlanStep(
                step_id=f"replan_{replan_number}_{evidence_type}",
                agent=agent_by_type.get(evidence_type, "knowledge_agent"),
                objective=(
                    f"Run one bounded read-only evidence query for missing {evidence_type}."
                ),
                required_evidence_types=[evidence_type],
            )
            for evidence_type in critic.missing_evidence_types
        ]
        return ReplanRequest(
            replan_id=f"replan_{replan_number}",
            reason=critic.rationale,
            missing_evidence_types=critic.missing_evidence_types,
            original_step_ids=[step.step_id for step in plan.steps],
            revised_steps=revised_steps,
            added_step_ids=[step.step_id for step in revised_steps],
            removed_step_ids=[],
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
        conflicts = cls._find_conflicts(alert, observed)
        sufficient = not missing and not conflicts
        can_query = bool(set(missing) & cls.REACT_QUERYABLE_TYPES)
        requires_react = sufficient is False and can_query and replan_count < max_replans
        if sufficient:
            rationale = (
                "Required evidence types are present and no unresolved conflict blocks policy."
            )
        elif requires_react:
            rationale = (
                f"Missing {', '.join(missing)}; enter the bounded read-only ReAct subgraph."
            )
        else:
            rationale = (
                "Evidence remains incomplete or conflicted after the bounded collection budget; "
                "preserve unknowns and require human review."
            )
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
        if "evidence-conflict" in labels:
            return {"network_flow", "process_tree", "identity_event", "change_record"}
        if "candidate-false-positive" in labels:
            return {"network_flow", "asset_record", "change_record"}
        if "lateral-movement" in labels:
            return {"network_flow", "process_tree", "identity_event"}
        if "missing-endpoint-evidence" in labels:
            return {"network_flow", "process_tree", "identity_event"}
        if "outbound" in labels:
            return {"network_flow", "process_tree"}
        return {"network_flow"}

    @staticmethod
    def _find_conflicts(alert: NormalizedAlert, observed: set[str]) -> list[str]:
        if (
            "evidence-conflict" in alert.labels
            and "network_flow" in observed
            and {"change_record", "asset_record"} & observed
        ):
            return [
                "Suspicious network behavior is contradicted by authorized operations context; "
                "the conflict must remain visible and blocks automatic high-risk response."
            ]
        return []
