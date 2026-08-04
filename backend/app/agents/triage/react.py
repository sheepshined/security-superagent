from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.adapters.models import DataClassification
from app.agents.triage.investigation_tools import (
    InvestigationToolDeniedError,
    ReadOnlyInvestigationToolRegistry,
)
from app.agents.triage.structured import ReActToolDecision, StructuredTriageAnalyzer
from app.domain.models import (
    Citation,
    CriticReport,
    Evidence,
    NormalizedAlert,
    ReActAuditStep,
)


class BoundedReActState(TypedDict, total=False):
    alert: NormalizedAlert
    evidence: list[Evidence]
    citations: list[Citation]
    critic: CriticReport
    data_classification: DataClassification
    attempted_tools: list[str]
    react_steps: list[ReActAuditStep]
    current_action: ReActToolDecision | None
    iterations: int
    tool_calls: int
    max_iterations: int
    max_tool_calls: int
    stop_reason: str | None


class BoundedReActSubgraph:
    """A small ReAct loop with an immutable tool allowlist and explicit budgets."""

    _TARGET_TOOLS = frozenset(
        {
            "query_network_context",
            "query_endpoint_context",
            "query_identity_context",
        }
    )
    _TARGET_ALIASES = frozenset({"target", "host_id", "entity_id"})

    def __init__(
        self,
        *,
        tools: ReadOnlyInvestigationToolRegistry,
        analyzer: StructuredTriageAnalyzer | None = None,
    ) -> None:
        self._tools = tools
        self._analyzer = analyzer
        builder = StateGraph(BoundedReActState)
        builder.add_node("select_readonly_action", self._select_action)
        builder.add_node("execute_readonly_tool", self._execute_tool)
        builder.add_edge(START, "select_readonly_action")
        builder.add_conditional_edges(
            "select_readonly_action",
            self._route_after_selection,
            {"execute": "execute_readonly_tool", "stop": END},
        )
        builder.add_conditional_edges(
            "execute_readonly_tool",
            self._route_after_execution,
            {"continue": "select_readonly_action", "stop": END},
        )
        self.graph = builder.compile()

    def run(self, state: BoundedReActState) -> BoundedReActState:
        return self.graph.invoke(state)

    def _select_action(self, state: BoundedReActState) -> dict[str, Any]:
        if (
            state["iterations"] >= state["max_iterations"]
            or state["tool_calls"] >= state["max_tool_calls"]
        ):
            return {
                "current_action": None,
                "stop_reason": (
                    "iteration_limit"
                    if state["iterations"] >= state["max_iterations"]
                    else "tool_call_limit"
                ),
            }
        action = self._model_action(state) or self._deterministic_action(state)
        if action is None or action.tool_name == "finish":
            return {"current_action": None, "stop_reason": "evidence_exhausted"}
        if action.tool_name in state["attempted_tools"]:
            action = self._deterministic_action(state)
        if action is None:
            return {"current_action": None, "stop_reason": "evidence_exhausted"}
        return {"current_action": action, "stop_reason": None}

    def _model_action(self, state: BoundedReActState) -> ReActToolDecision | None:
        if self._analyzer is None:
            return None
        try:
            action = self._analyzer.select_react_action(
                alert=state["alert"],
                critic=state["critic"],
                attempted_tools=state["attempted_tools"],
                remaining_iterations=state["max_iterations"] - state["iterations"],
                data_classification=state["data_classification"],
            )
            return self._normalize_model_action(action, state["alert"])
        except Exception:
            return None

    @classmethod
    def _normalize_model_action(
        cls,
        action: ReActToolDecision,
        alert: NormalizedAlert,
    ) -> ReActToolDecision | None:
        """Normalize safe aliases and reject inputs outside the approved schemas."""
        if action.tool_name == "finish":
            return action.model_copy(update={"tool_input": {}})
        if action.tool_name == "search_security_knowledge":
            if set(action.tool_input) - {"query"}:
                return None
            query = action.tool_input.get("query", "").strip()
            if not query:
                return None
            return action.model_copy(update={"tool_input": {"query": query}})
        if action.tool_name not in cls._TARGET_TOOLS:
            return None
        if set(action.tool_input) - cls._TARGET_ALIASES:
            return None
        candidates = {
            value.strip()
            for key, value in action.tool_input.items()
            if key in cls._TARGET_ALIASES and value.strip()
        }
        if len(candidates) != 1:
            return None
        target = candidates.pop()
        allowed_targets = {
            *(entity.id for entity in alert.entities),
            *(indicator.value for indicator in alert.indicators),
        }
        if target not in allowed_targets:
            return None
        return action.model_copy(update={"tool_input": {"target": target}})

    def _deterministic_action(
        self,
        state: BoundedReActState,
    ) -> ReActToolDecision | None:
        attempted = set(state["attempted_tools"])
        missing = set(state["critic"].missing_evidence_types)
        target = self._default_target(state["alert"])
        candidates = [
            (
                "network_flow",
                "query_network_context",
                "补充网络通信上下文。",
            ),
            (
                "process_tree",
                "query_endpoint_context",
                "补充端点进程树和遥测状态。",
            ),
            (
                "identity_event",
                "query_identity_context",
                "补充账号与认证上下文。",
            ),
        ]
        for evidence_type, tool_name, summary in candidates:
            if evidence_type in missing and tool_name not in attempted:
                return ReActToolDecision(
                    tool_name=tool_name,
                    tool_input={"target": target},
                    decision_summary=summary,
                )
        if not state["citations"] and "search_security_knowledge" not in attempted:
            return ReActToolDecision(
                tool_name="search_security_knowledge",
                tool_input={"query": state["alert"].title},
                decision_summary="补充与当前告警相关的公开安全知识。",
            )
        return None

    def _execute_tool(self, state: BoundedReActState) -> dict[str, Any]:
        action = state["current_action"]
        if action is None:
            return {"stop_reason": "evidence_exhausted"}
        iteration = state["iterations"] + 1
        tool_calls = state["tool_calls"] + 1
        existing_evidence = {item.evidence_id for item in state["evidence"]}
        existing_citations = {item.citation_id for item in state["citations"]}
        status = "completed"
        stop_reason = None
        try:
            observation = self._tools.invoke(
                tool_name=action.tool_name,
                tool_input=action.tool_input,
                alert=state["alert"],
            )
            new_evidence = [
                item for item in observation.evidence if item.evidence_id not in existing_evidence
            ]
            new_citations = [
                item
                for item in observation.citations
                if item.citation_id not in existing_citations
            ]
            observation_summary = observation.summary
        except InvestigationToolDeniedError as exc:
            new_evidence = []
            new_citations = []
            observation_summary = str(exc)
            status = "denied"
            stop_reason = "tool_denied"
        except Exception as exc:
            new_evidence = []
            new_citations = []
            observation_summary = f"只读工具调用失败：{type(exc).__name__}"
            status = "failed"
        evidence = [*state["evidence"], *new_evidence]
        citations = [*state["citations"], *new_citations]
        if stop_reason is None and self._missing_types_resolved(state["critic"], evidence):
            stop_reason = "evidence_acquired"
        elif stop_reason is None and tool_calls >= state["max_tool_calls"]:
            stop_reason = "tool_call_limit"
        elif stop_reason is None and iteration >= state["max_iterations"]:
            stop_reason = "iteration_limit"
        audit_step = ReActAuditStep(
            iteration=iteration,
            decision_summary=action.decision_summary,
            tool_name=action.tool_name,
            tool_input=action.tool_input,
            observation_summary=observation_summary,
            new_evidence_ids=[item.evidence_id for item in new_evidence],
            status=status,
        )
        return {
            "evidence": evidence,
            "citations": citations,
            "attempted_tools": [*state["attempted_tools"], action.tool_name],
            "react_steps": [*state["react_steps"], audit_step],
            "iterations": iteration,
            "tool_calls": tool_calls,
            "stop_reason": stop_reason,
        }

    @staticmethod
    def _route_after_selection(state: BoundedReActState) -> str:
        return "stop" if state.get("current_action") is None else "execute"

    @staticmethod
    def _route_after_execution(state: BoundedReActState) -> str:
        return "stop" if state.get("stop_reason") else "continue"

    @staticmethod
    def _missing_types_resolved(
        critic: CriticReport,
        evidence: list[Evidence],
    ) -> bool:
        observed = {item.type for item in evidence}
        return set(critic.missing_evidence_types) <= observed

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
