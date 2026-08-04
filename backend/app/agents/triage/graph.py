from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import StateSnapshot

from app.adapters.models import DataClassification
from app.adapters.protocols import XDRAdapter
from app.agents.triage.investigation_tools import (
    InvestigationToolDeniedError,
    ReadOnlyInvestigationToolRegistry,
)
from app.agents.triage.planning import EvidenceCritic, InvestigationPlanner
from app.agents.triage.react import BoundedReActSubgraph
from app.agents.triage.structured import StructuredTriageAnalyzer
from app.agents.triage.tools import create_knowledge_search_tool
from app.domain.enums import ModelHealth, Verdict
from app.domain.models import (
    AlertRef,
    Citation,
    CriticReport,
    Evidence,
    InvestigationPlan,
    InvestigationTrace,
    NormalizedAlert,
    PlanStepExecution,
    ReActAuditStep,
    ReplanRequest,
    TraceCheckpoint,
    TraceReplay,
    TriageResult,
)
from app.observability.langsmith import LangSmithTracePolicy
from app.rag.hybrid import HybridRetriever
from app.services.triage import TriageService


class TriageGraphState(TypedDict, total=False):
    job_id: str
    case_id: str
    trace_id: str
    alert_ref: AlertRef
    data_classification: DataClassification
    alert: NormalizedAlert
    evidence: list[Evidence]
    citations: list[Citation]
    investigation_plan: InvestigationPlan
    critic_report: CriticReport
    react_steps: list[ReActAuditStep]
    react_attempted_tools: list[str]
    react_iterations: int
    react_tool_calls: int
    react_stop_reason: str | None
    replan_count: int
    replan_history: list[ReplanRequest]
    plan_executions: list[PlanStepExecution]
    model_assessment: dict[str, Any] | None
    model_error: str | None
    result: TriageResult
    workflow_trace: list[str]


ProgressHook = Callable[[str, str, int, dict[str, object]], None]
_progress_hook: ContextVar[ProgressHook | None] = ContextVar(
    "triage_progress_hook",
    default=None,
)


class TriageGraphRunner:
    """Evidence-driven PEVR workflow with a bounded, read-only ReAct subgraph."""

    def __init__(
        self,
        *,
        xdr: XDRAdapter,
        retriever: HybridRetriever,
        trace_policy: LangSmithTracePolicy,
        analyzer: StructuredTriageAnalyzer | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        self._xdr = xdr
        self._triage = TriageService(xdr, retriever)
        self._planner = InvestigationPlanner()
        self._knowledge_tool = create_knowledge_search_tool(retriever)
        self._tools = ReadOnlyInvestigationToolRegistry(xdr, retriever)
        self._react = BoundedReActSubgraph(
            tools=self._tools,
            analyzer=analyzer,
        )
        self._trace_policy = trace_policy
        self._analyzer = analyzer
        self._checkpointer = checkpointer or InMemorySaver()
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(TriageGraphState)
        builder.add_node("load_alert", self._load_alert)
        builder.add_node("plan_investigation", self._plan_investigation)
        builder.add_node("execute_initial_plan", self._execute_initial_plan)
        builder.add_node("retrieve_knowledge", self._retrieve_knowledge)
        builder.add_node("structured_assessment", self._structured_assessment)
        builder.add_node("critic_verify", self._critic_verify)
        builder.add_node("revise_plan", self._revise_plan)
        builder.add_node("bounded_react", self._bounded_react)
        builder.add_node("policy_finalize", self._policy_finalize)
        builder.add_edge(START, "load_alert")
        builder.add_edge("load_alert", "plan_investigation")
        builder.add_edge("plan_investigation", "execute_initial_plan")
        builder.add_edge("execute_initial_plan", "retrieve_knowledge")
        builder.add_edge("retrieve_knowledge", "structured_assessment")
        builder.add_edge("structured_assessment", "critic_verify")
        builder.add_conditional_edges(
            "critic_verify",
            self._route_after_critic,
            {"replan": "revise_plan", "finalize": "policy_finalize"},
        )
        builder.add_edge("revise_plan", "bounded_react")
        builder.add_edge("bounded_react", "structured_assessment")
        builder.add_edge("policy_finalize", END)
        return builder.compile(checkpointer=self._checkpointer)

    def run(
        self,
        *,
        job_id: str,
        case_id: str,
        trace_id: str,
        alert_ref: AlertRef,
        data_classification: DataClassification,
    ) -> TriageResult:
        config = {
            "configurable": {"thread_id": trace_id},
            "metadata": {
                "job_id": job_id,
                "case_id": case_id,
                "data_classification": data_classification.value,
                "agent_strategy": "plan_execute_verify_replan_bounded_react",
            },
        }
        with self._trace_policy.trace(
            trace_id=trace_id,
            data_classification=data_classification,
            metadata={
                "job_id": job_id,
                "case_id": case_id,
                "agent_strategy": "pevr_bounded_react",
            },
        ):
            state = self.graph.invoke(
                {
                    "job_id": job_id,
                    "case_id": case_id,
                    "trace_id": trace_id,
                    "alert_ref": alert_ref,
                    "data_classification": data_classification,
                    "evidence": [],
                    "citations": [],
                    "react_steps": [],
                    "react_attempted_tools": [],
                    "react_iterations": 0,
                    "react_tool_calls": 0,
                    "react_stop_reason": None,
                    "replan_count": 0,
                    "replan_history": [],
                    "plan_executions": [],
                    "workflow_trace": [],
                },
                config=config,
            )
        return state["result"]

    async def arun(
        self,
        *,
        job_id: str,
        case_id: str,
        trace_id: str,
        alert_ref: AlertRef,
        data_classification: DataClassification,
        progress_hook: ProgressHook | None = None,
    ) -> TriageResult:
        config = self._run_config(
            job_id=job_id,
            case_id=case_id,
            trace_id=trace_id,
            data_classification=data_classification,
        )
        token = _progress_hook.set(progress_hook)
        try:
            with self._trace_policy.trace(
                trace_id=trace_id,
                data_classification=data_classification,
                metadata={
                    "job_id": job_id,
                    "case_id": case_id,
                    "agent_strategy": "pevr_bounded_react",
                },
            ):
                state = await self.graph.ainvoke(
                    self._initial_state(
                        job_id=job_id,
                        case_id=case_id,
                        trace_id=trace_id,
                        alert_ref=alert_ref,
                        data_classification=data_classification,
                    ),
                    config=config,
                )
            return state["result"]
        finally:
            _progress_hook.reset(token)

    def checkpoint(self, trace_id: str) -> TriageGraphState:
        snapshot = self.graph.get_state({"configurable": {"thread_id": trace_id}})
        return dict(snapshot.values)

    def trace_replay(self, trace_id: str) -> TraceReplay:
        snapshots = list(self.graph.get_state_history({"configurable": {"thread_id": trace_id}}))
        checkpoints = [
            self._trace_checkpoint(trace_id, snapshot)
            for snapshot in reversed(snapshots)
            if int(snapshot.metadata.get("step", -1)) >= 0
        ]
        return TraceReplay(
            trace_id=trace_id,
            checkpoint_count=len(checkpoints),
            checkpoints=checkpoints,
        )

    def close(self) -> None:
        connection = getattr(self._checkpointer, "conn", None)
        if connection is not None:
            connection.close()

    def _load_alert(self, state: TriageGraphState) -> dict[str, Any]:
        alert = self._xdr.get_alert(state["alert_ref"])
        self._notify("node_completed", "load_alert", 28, {"agent": "supervisor"})
        return {
            "alert": alert,
            "workflow_trace": [*state["workflow_trace"], "load_alert"],
        }

    def _plan_investigation(self, state: TriageGraphState) -> dict[str, Any]:
        plan = self._planner.create_plan(state["alert"])
        self._notify(
            "plan_created",
            "plan_investigation",
            36,
            {"step_count": len(plan.steps), "agent": "supervisor"},
        )
        return {
            "investigation_plan": plan,
            "workflow_trace": [*state["workflow_trace"], "plan_investigation"],
        }

    def _execute_initial_plan(self, state: TriageGraphState) -> dict[str, Any]:
        evidence: list[Evidence] = []
        citations: list[Citation] = []
        executions: list[PlanStepExecution] = []
        seen_evidence: set[str] = set()
        seen_citations: set[str] = set()
        for step in state["investigation_plan"].steps:
            tool_name, tool_input = self._tool_for_plan_step(step, state["alert"])
            status = "completed"
            try:
                observation = self._tools.invoke(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    alert=state["alert"],
                )
                new_evidence = [
                    item for item in observation.evidence if item.evidence_id not in seen_evidence
                ]
                new_citations = [
                    item for item in observation.citations if item.citation_id not in seen_citations
                ]
                summary = observation.summary
            except InvestigationToolDeniedError as exc:
                new_evidence = []
                new_citations = []
                summary = str(exc)
                status = "denied"
            except Exception as exc:
                new_evidence = []
                new_citations = []
                summary = f"只读计划步骤失败：{type(exc).__name__}"
                status = "failed"
            evidence.extend(new_evidence)
            citations.extend(new_citations)
            seen_evidence.update(item.evidence_id for item in new_evidence)
            seen_citations.update(item.citation_id for item in new_citations)
            executions.append(
                PlanStepExecution(
                    step_id=step.step_id,
                    agent=step.agent,
                    tool_name=tool_name,
                    status=status,
                    evidence_ids=[item.evidence_id for item in new_evidence],
                    citation_ids=[item.citation_id for item in new_citations],
                    observation_summary=summary,
                )
            )
            self._notify(
                "agent_step_completed",
                "execute_initial_plan",
                min(48, 38 + len(executions) * 3),
                {
                    "step_id": step.step_id,
                    "agent": step.agent,
                    "tool": tool_name,
                    "status": status,
                },
            )
        self._notify(
            "agent_completed",
            "execute_initial_plan",
            48,
            {
                "evidence_count": len(evidence),
                "citation_count": len(citations),
                "step_count": len(executions),
                "agent": "investigation",
            },
        )
        return {
            "evidence": evidence,
            "citations": citations,
            "plan_executions": executions,
            "workflow_trace": [*state["workflow_trace"], "execute_initial_plan"],
        }

    def _retrieve_knowledge(self, state: TriageGraphState) -> dict[str, Any]:
        if state["citations"]:
            self._notify(
                "tool_completed",
                "retrieve_knowledge",
                58,
                {
                    "tool": "search_security_knowledge",
                    "citation_count": len(state["citations"]),
                    "reused_plan_result": True,
                },
            )
            return {
                "citations": state["citations"],
                "workflow_trace": [*state["workflow_trace"], "retrieve_knowledge"],
            }
        alert = state["alert"]
        query = " ".join(
            [
                alert.title,
                *alert.labels,
                *(item.statement for item in state["evidence"]),
            ]
        )
        hits = self._knowledge_tool.invoke({"query": query, "top_k": 4})
        citations = [
            Citation(
                citation_id=f"citation_{hit['chunk_id'].replace(':', '_')}",
                document_id=hit["document_id"],
                chunk_id=hit["chunk_id"],
                title=hit["title"],
                source_uri=hit["source_uri"],
                version=hit["version"],
                excerpt=hit["content"],
            )
            for hit in hits
            if hit["final_score"] >= 0.15
        ]
        self._notify(
            "tool_completed",
            "retrieve_knowledge",
            58,
            {"tool": "search_security_knowledge", "citation_count": len(citations)},
        )
        return {
            "citations": citations,
            "workflow_trace": [*state["workflow_trace"], "retrieve_knowledge"],
        }

    def _structured_assessment(self, state: TriageGraphState) -> dict[str, Any]:
        history = [*state["workflow_trace"], "structured_assessment"]
        if self._analyzer is None:
            self._notify(
                "model_skipped",
                "structured_assessment",
                68,
                {"reason": "model_not_configured"},
            )
            return {
                "model_assessment": None,
                "model_error": "model_assessment_disabled",
                "workflow_trace": history,
            }
        try:
            assessment = self._analyzer.analyze(
                alert=state["alert"],
                evidence=state["evidence"],
                citations=state["citations"],
                data_classification=state["data_classification"],
            )
            self._notify(
                "model_completed",
                "structured_assessment",
                68,
                {"provider": assessment.get("provider", "unknown")},
            )
            return {
                "model_assessment": assessment,
                "model_error": None,
                "workflow_trace": history,
            }
        except Exception as exc:
            self._notify(
                "model_degraded",
                "structured_assessment",
                68,
                {"error_type": type(exc).__name__},
            )
            return {
                "model_assessment": None,
                "model_error": type(exc).__name__,
                "workflow_trace": history,
            }

    def _critic_verify(self, state: TriageGraphState) -> dict[str, Any]:
        report = EvidenceCritic.review(
            alert=state["alert"],
            evidence=state["evidence"],
            replan_count=state["replan_count"],
            max_replans=state["investigation_plan"].max_replans,
        )
        self._notify(
            "critic_completed",
            "critic_verify",
            78,
            {
                "evidence_sufficient": report.evidence_sufficient,
                "requires_replan": report.requires_react,
            },
        )
        return {
            "critic_report": report,
            "workflow_trace": [*state["workflow_trace"], "critic_verify"],
        }

    def _bounded_react(self, state: TriageGraphState) -> dict[str, Any]:
        plan = state["investigation_plan"]
        react_state = self._react.run(
            {
                "alert": state["alert"],
                "evidence": state["evidence"],
                "citations": state["citations"],
                "critic": state["critic_report"],
                "data_classification": state["data_classification"],
                "attempted_tools": state["react_attempted_tools"],
                "react_steps": state["react_steps"],
                "iterations": state["react_iterations"],
                "tool_calls": state["react_tool_calls"],
                "max_iterations": plan.max_react_iterations,
                "max_tool_calls": plan.max_tool_calls,
                "stop_reason": None,
            }
        )
        self._notify(
            "replan_completed",
            "bounded_react",
            86,
            {
                "tool_calls": react_state["tool_calls"],
                "stop_reason": react_state["stop_reason"],
            },
        )
        return {
            "evidence": react_state["evidence"],
            "citations": react_state["citations"],
            "react_steps": react_state["react_steps"],
            "react_attempted_tools": react_state["attempted_tools"],
            "react_iterations": react_state["iterations"],
            "react_tool_calls": react_state["tool_calls"],
            "react_stop_reason": react_state["stop_reason"],
            "workflow_trace": [*state["workflow_trace"], "bounded_react"],
        }

    def _revise_plan(self, state: TriageGraphState) -> dict[str, Any]:
        replan_number = state["replan_count"] + 1
        request = self._planner.create_replan(
            plan=state["investigation_plan"],
            critic=state["critic_report"],
            replan_number=replan_number,
        )
        self._notify(
            "replan_created",
            "revise_plan",
            82,
            {
                "replan_id": request.replan_id,
                "added_step_ids": request.added_step_ids,
                "removed_step_ids": request.removed_step_ids,
            },
        )
        return {
            "replan_count": replan_number,
            "replan_history": [*state["replan_history"], request],
            "workflow_trace": [*state["workflow_trace"], "revise_plan"],
        }

    @staticmethod
    def _route_after_critic(state: TriageGraphState) -> str:
        return "replan" if state["critic_report"].requires_react else "finalize"

    def _policy_finalize(self, state: TriageGraphState) -> dict[str, Any]:
        workflow_trace = [*state["workflow_trace"], "policy_finalize"]
        investigation = InvestigationTrace(
            plan=state["investigation_plan"],
            plan_executions=state["plan_executions"],
            critic=state["critic_report"],
            react_steps=state["react_steps"],
            replans=state["replan_count"],
            tool_calls=state["react_tool_calls"],
            replan_history=state["replan_history"],
            stop_reason=self._stop_reason(state),
        )
        result = self._triage.build_result(
            job_id=state["job_id"],
            case_id=state["case_id"],
            alert=state["alert"],
            evidence=state["evidence"],
            citations=state["citations"],
            workflow_trace=workflow_trace,
            model_assessment=state.get("model_assessment"),
            investigation=investigation,
        )
        if self._analyzer is None:
            result = result.model_copy(update={"model_health": ModelHealth.NOT_CONFIGURED})
        elif state.get("model_error"):
            result = result.model_copy(
                update={
                    "verdict": Verdict.NEEDS_REVIEW,
                    "confidence": min(result.confidence, 0.5),
                    "recommended_actions": [],
                    "model_health": ModelHealth.DEGRADED,
                    "degraded_reason": f"model_error:{state['model_error']}",
                }
            )
        else:
            result = result.model_copy(update={"model_health": ModelHealth.HEALTHY})
        result = result.model_copy(update={"replan_history": state["replan_history"]})
        self._notify(
            "policy_finalized",
            "policy_finalize",
            95,
            {"verdict": result.verdict.value, "model_health": result.model_health.value},
        )
        return {"result": result, "workflow_trace": workflow_trace}

    @staticmethod
    def _notify(
        event_type: str,
        node: str,
        progress: int,
        data: dict[str, object],
    ) -> None:
        hook = _progress_hook.get()
        if hook is not None:
            hook(event_type, node, progress, data)

    @staticmethod
    def _run_config(
        *,
        job_id: str,
        case_id: str,
        trace_id: str,
        data_classification: DataClassification,
    ) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": trace_id},
            "metadata": {
                "job_id": job_id,
                "case_id": case_id,
                "data_classification": data_classification.value,
                "agent_strategy": "plan_execute_verify_replan_bounded_react",
            },
        }

    @staticmethod
    def _initial_state(
        *,
        job_id: str,
        case_id: str,
        trace_id: str,
        alert_ref: AlertRef,
        data_classification: DataClassification,
    ) -> TriageGraphState:
        return {
            "job_id": job_id,
            "case_id": case_id,
            "trace_id": trace_id,
            "alert_ref": alert_ref,
            "data_classification": data_classification,
            "evidence": [],
            "citations": [],
            "react_steps": [],
            "react_attempted_tools": [],
            "react_iterations": 0,
            "react_tool_calls": 0,
            "react_stop_reason": None,
            "replan_count": 0,
            "replan_history": [],
            "plan_executions": [],
            "workflow_trace": [],
        }

    @staticmethod
    def _tool_for_plan_step(
        step: Any,
        alert: NormalizedAlert,
    ) -> tuple[str, dict[str, str]]:
        if step.agent == "knowledge_agent":
            return "search_security_knowledge", {"query": alert.title}
        target = TriageGraphRunner._default_target(alert)
        tool_by_agent = {
            "network_agent": "query_network_context",
            "endpoint_agent": "query_endpoint_context",
            "identity_agent": "query_identity_context",
        }
        return tool_by_agent[step.agent], {"target": target}

    @staticmethod
    def _default_target(alert: NormalizedAlert) -> str:
        source_host = next(
            (
                entity.id
                for entity in alert.entities
                if entity.type == "host" and entity.role == "source"
            ),
            None,
        )
        if source_host:
            return source_host
        if alert.entities:
            return alert.entities[0].id
        if alert.indicators:
            return alert.indicators[0].value
        return alert.external_id

    @staticmethod
    def _stop_reason(state: TriageGraphState) -> str:
        if state["replan_count"] == 0:
            return (
                "not_required"
                if state["critic_report"].evidence_sufficient
                else "human_review_required"
            )
        if state["critic_report"].evidence_sufficient:
            return "evidence_acquired"
        return state.get("react_stop_reason") or "human_review_required"

    @staticmethod
    def _trace_checkpoint(trace_id: str, snapshot: StateSnapshot) -> TraceCheckpoint:
        values = snapshot.values
        workflow_trace = values.get("workflow_trace", [])
        result = values.get("result")
        if isinstance(result, TriageResult):
            verdict = result.verdict
        elif isinstance(result, dict):
            verdict = result.get("verdict")
        else:
            verdict = None
        configurable = snapshot.config.get("configurable", {})
        return TraceCheckpoint(
            checkpoint_id=configurable["checkpoint_id"],
            trace_id=trace_id,
            created_at=snapshot.created_at,
            step=int(snapshot.metadata.get("step", -1)),
            source=str(snapshot.metadata.get("source", "loop")),
            last_node=workflow_trace[-1] if workflow_trace else None,
            next_nodes=list(snapshot.next),
            evidence_count=len(values.get("evidence", [])),
            citation_count=len(values.get("citations", [])),
            react_tool_calls=int(values.get("react_tool_calls", 0)),
            verdict=verdict,
        )
