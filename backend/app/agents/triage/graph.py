from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import StateSnapshot

from app.adapters.models import DataClassification
from app.adapters.protocols import XDRAdapter
from app.agents.triage.investigation_tools import ReadOnlyInvestigationToolRegistry
from app.agents.triage.planning import EvidenceCritic, InvestigationPlanner
from app.agents.triage.react import BoundedReActSubgraph
from app.agents.triage.structured import StructuredTriageAnalyzer
from app.agents.triage.tools import create_knowledge_search_tool
from app.domain.models import (
    AlertRef,
    Citation,
    CriticReport,
    Evidence,
    InvestigationPlan,
    InvestigationTrace,
    NormalizedAlert,
    ReActAuditStep,
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
    model_assessment: dict[str, Any] | None
    model_error: str | None
    result: TriageResult
    workflow_trace: list[str]


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
        self._react = BoundedReActSubgraph(
            tools=ReadOnlyInvestigationToolRegistry(xdr, retriever),
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
            {"react": "bounded_react", "finalize": "policy_finalize"},
        )
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
                    "workflow_trace": [],
                },
                config=config,
            )
        return state["result"]

    def checkpoint(self, trace_id: str) -> TriageGraphState:
        snapshot = self.graph.get_state({"configurable": {"thread_id": trace_id}})
        return dict(snapshot.values)

    def trace_replay(self, trace_id: str) -> TraceReplay:
        snapshots = list(
            self.graph.get_state_history({"configurable": {"thread_id": trace_id}})
        )
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
        return {
            "alert": alert,
            "workflow_trace": [*state["workflow_trace"], "load_alert"],
        }

    def _plan_investigation(self, state: TriageGraphState) -> dict[str, Any]:
        plan = self._planner.create_plan(state["alert"])
        return {
            "investigation_plan": plan,
            "workflow_trace": [*state["workflow_trace"], "plan_investigation"],
        }

    def _execute_initial_plan(self, state: TriageGraphState) -> dict[str, Any]:
        evidence = self._xdr.get_evidence(state["alert"])
        return {
            "evidence": evidence,
            "workflow_trace": [*state["workflow_trace"], "execute_initial_plan"],
        }

    def _retrieve_knowledge(self, state: TriageGraphState) -> dict[str, Any]:
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
        return {
            "citations": citations,
            "workflow_trace": [*state["workflow_trace"], "retrieve_knowledge"],
        }

    def _structured_assessment(self, state: TriageGraphState) -> dict[str, Any]:
        history = [*state["workflow_trace"], "structured_assessment"]
        if self._analyzer is None:
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
            return {
                "model_assessment": assessment,
                "model_error": None,
                "workflow_trace": history,
            }
        except Exception as exc:
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
        return {
            "evidence": react_state["evidence"],
            "citations": react_state["citations"],
            "react_steps": react_state["react_steps"],
            "react_attempted_tools": react_state["attempted_tools"],
            "react_iterations": react_state["iterations"],
            "react_tool_calls": react_state["tool_calls"],
            "react_stop_reason": react_state["stop_reason"],
            "replan_count": state["replan_count"] + 1,
            "workflow_trace": [*state["workflow_trace"], "bounded_react"],
        }

    @staticmethod
    def _route_after_critic(state: TriageGraphState) -> str:
        return "react" if state["critic_report"].requires_react else "finalize"

    def _policy_finalize(self, state: TriageGraphState) -> dict[str, Any]:
        workflow_trace = [*state["workflow_trace"], "policy_finalize"]
        investigation = InvestigationTrace(
            plan=state["investigation_plan"],
            critic=state["critic_report"],
            react_steps=state["react_steps"],
            replans=state["replan_count"],
            tool_calls=state["react_tool_calls"],
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
        return {"result": result, "workflow_trace": workflow_trace}

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
