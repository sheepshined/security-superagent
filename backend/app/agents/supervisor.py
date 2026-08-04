from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app.agents.triage.investigation_tools import (
    InvestigationToolDeniedError,
    ReadOnlyInvestigationToolRegistry,
)
from app.domain.models import (
    AgentReport,
    Citation,
    Evidence,
    EvidenceConflict,
    InvestigationPlan,
    InvestigationPlanStep,
    NormalizedAlert,
    PlanStepExecution,
    utc_now,
)

AgentProgress = Callable[[str, PlanStepExecution], None]


@dataclass(slots=True)
class SupervisorExecution:
    evidence: list[Evidence]
    citations: list[Citation]
    executions: list[PlanStepExecution]
    agent_reports: list[AgentReport]
    conflicts: list[EvidenceConflict]


class InvestigationSupervisor:
    """Run specialist read-only plans concurrently and merge auditable reports."""

    AGENT_ORDER = ("network_agent", "endpoint_agent", "identity_agent", "knowledge_agent")

    def __init__(self, tools: ReadOnlyInvestigationToolRegistry) -> None:
        self.tools = tools

    def execute(
        self,
        *,
        plan: InvestigationPlan,
        alert: NormalizedAlert,
        progress: AgentProgress | None = None,
    ) -> SupervisorExecution:
        groups: dict[str, list[InvestigationPlanStep]] = {}
        for step in plan.steps:
            groups.setdefault(step.agent, []).append(step)

        results: dict[
            str,
            tuple[list[Evidence], list[Citation], list[PlanStepExecution], AgentReport],
        ] = {}
        specialist_groups = {
            agent: steps for agent, steps in groups.items() if agent != "knowledge_agent"
        }
        if specialist_groups:
            with ThreadPoolExecutor(
                max_workers=min(3, len(specialist_groups)),
                thread_name_prefix="specialist-agent",
            ) as executor:
                futures = {
                    agent: executor.submit(self._run_agent, agent, steps, alert)
                    for agent, steps in specialist_groups.items()
                }
                for agent, future in futures.items():
                    results[agent] = future.result()

        # Knowledge is a shared capability, kept outside the three domain slots.
        if "knowledge_agent" in groups:
            results["knowledge_agent"] = self._run_agent(
                "knowledge_agent",
                groups["knowledge_agent"],
                alert,
            )

        evidence: list[Evidence] = []
        citations: list[Citation] = []
        executions: list[PlanStepExecution] = []
        reports: list[AgentReport] = []
        seen_evidence: set[str] = set()
        seen_citations: set[str] = set()
        for agent in self.AGENT_ORDER:
            if agent not in results:
                continue
            agent_evidence, agent_citations, agent_executions, report = results[agent]
            for item in agent_evidence:
                if item.evidence_id not in seen_evidence:
                    seen_evidence.add(item.evidence_id)
                    evidence.append(item)
            for item in agent_citations:
                if item.citation_id not in seen_citations:
                    seen_citations.add(item.citation_id)
                    citations.append(item)
            executions.extend(agent_executions)
            reports.append(report)
            if progress:
                for execution in agent_executions:
                    progress(agent, execution)

        return SupervisorExecution(
            evidence=evidence,
            citations=citations,
            executions=executions,
            agent_reports=reports,
            conflicts=self._find_conflicts(alert, evidence),
        )

    def _run_agent(
        self,
        agent: str,
        steps: list[InvestigationPlanStep],
        alert: NormalizedAlert,
    ) -> tuple[list[Evidence], list[Citation], list[PlanStepExecution], AgentReport]:
        started_at = utc_now()
        evidence: list[Evidence] = []
        citations: list[Citation] = []
        executions: list[PlanStepExecution] = []
        observations: list[str] = []
        unknowns: list[str] = []
        seen_evidence: set[str] = set()
        seen_citations: set[str] = set()

        for step in steps:
            tool_name, tool_input = self._tool_for_step(step, alert)
            status = "completed"
            try:
                observation = self.tools.invoke(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    alert=alert,
                )
                new_evidence = [
                    item for item in observation.evidence if item.evidence_id not in seen_evidence
                ]
                new_citations = [
                    item for item in observation.citations if item.citation_id not in seen_citations
                ]
                summary = observation.summary
                if not new_evidence and not new_citations:
                    unknowns.append(f"No usable result for: {step.objective}")
            except InvestigationToolDeniedError as exc:
                new_evidence = []
                new_citations = []
                summary = str(exc)
                status = "denied"
                unknowns.append(summary)
            except Exception as exc:
                new_evidence = []
                new_citations = []
                summary = f"Read-only plan step failed: {type(exc).__name__}"
                status = "failed"
                unknowns.append(summary)

            evidence.extend(new_evidence)
            citations.extend(new_citations)
            seen_evidence.update(item.evidence_id for item in new_evidence)
            seen_citations.update(item.citation_id for item in new_citations)
            observations.append(summary)
            executions.append(
                PlanStepExecution(
                    step_id=step.step_id,
                    agent=agent,
                    tool_name=tool_name,
                    status=status,
                    evidence_ids=[item.evidence_id for item in new_evidence],
                    citation_ids=[item.citation_id for item in new_citations],
                    observation_summary=summary,
                )
            )

        report_status = (
            "failed"
            if executions and all(item.status == "failed" for item in executions)
            else "partial"
            if unknowns
            else "completed"
        )
        report = AgentReport(
            agent=agent,
            objective="; ".join(step.objective for step in steps),
            status=report_status,
            evidence_ids=sorted(seen_evidence),
            citation_ids=sorted(seen_citations),
            observations=observations,
            unknowns=unknowns,
            tool_calls=len(executions),
            started_at=started_at,
            completed_at=utc_now(),
        )
        return evidence, citations, executions, report

    @staticmethod
    def _tool_for_step(
        step: InvestigationPlanStep,
        alert: NormalizedAlert,
    ) -> tuple[str, dict[str, str]]:
        if step.agent == "knowledge_agent":
            return "search_security_knowledge", {"query": " ".join([alert.title, *alert.labels])}
        tool_by_agent = {
            "network_agent": "query_network_context",
            "endpoint_agent": "query_endpoint_context",
            "identity_agent": "query_identity_context",
        }
        return tool_by_agent[step.agent], {"target": InvestigationSupervisor._default_target(alert)}

    @staticmethod
    def _default_target(alert: NormalizedAlert) -> str:
        for entity in alert.entities:
            if entity.type == "host" and entity.role == "source":
                return entity.id
        if alert.entities:
            return alert.entities[0].id
        if alert.indicators:
            return alert.indicators[0].value
        return alert.external_id

    @staticmethod
    def _find_conflicts(
        alert: NormalizedAlert,
        evidence: list[Evidence],
    ) -> list[EvidenceConflict]:
        if "evidence-conflict" not in alert.labels:
            return []
        suspicious = next((item for item in evidence if item.type == "network_flow"), None)
        authorized = next(
            (item for item in evidence if item.type in {"change_record", "asset_record"}),
            None,
        )
        identity = next((item for item in evidence if item.type == "identity_event"), None)
        if suspicious is None or authorized is None:
            return []
        supporting = [suspicious, authorized, *([identity] if identity else [])]
        return [
            EvidenceConflict(
                conflict_id=f"conflict_{alert.alert_id}_authorization",
                topic="Suspicious network behavior conflicts with authorized operations context",
                evidence_ids=[item.evidence_id for item in supporting],
                statements=[item.statement for item in supporting],
                status="resolved_as_authorized_activity",
                resolution=(
                    "Network behavior resembles lateral movement, but asset, change, or identity "
                    "context supports authorized operations. Preserve the conflict and prohibit "
                    "automatic high-risk response."
                ),
            )
        ]
