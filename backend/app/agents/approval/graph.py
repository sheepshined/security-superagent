from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class ApprovalState(TypedDict, total=False):
    action: dict[str, Any]
    decision: dict[str, Any]
    status: str


class ApprovalGraphRunner:
    """Small interruptible graph used before any future high-risk tool execution."""

    def __init__(self, checkpointer: BaseCheckpointSaver | None = None) -> None:
        builder = StateGraph(ApprovalState)
        builder.add_node("approval_gate", self._approval_gate)
        builder.add_node("record_decision", self._record_decision)
        builder.add_edge(START, "approval_gate")
        builder.add_edge("approval_gate", "record_decision")
        builder.add_edge("record_decision", END)
        self._checkpointer = checkpointer or InMemorySaver()
        self.graph = builder.compile(checkpointer=self._checkpointer)

    def request(self, *, thread_id: str, action: dict[str, Any]) -> dict[str, Any]:
        return self.graph.invoke(
            {"action": action, "status": "waiting_approval"},
            config={"configurable": {"thread_id": thread_id}},
        )

    def resume(
        self,
        *,
        thread_id: str,
        approved: bool,
        reviewer: str,
        comment: str = "",
    ) -> dict[str, Any]:
        return self.graph.invoke(
            Command(
                resume={
                    "approved": approved,
                    "reviewer": reviewer,
                    "comment": comment,
                }
            ),
            config={"configurable": {"thread_id": thread_id}},
        )

    def close(self) -> None:
        connection = getattr(self._checkpointer, "conn", None)
        if connection is not None:
            connection.close()

    @staticmethod
    def _approval_gate(state: ApprovalState) -> dict[str, Any]:
        decision = interrupt(
            {
                "kind": "high_risk_action_approval",
                "action": state["action"],
                "allowed_decisions": ["approve", "reject"],
            }
        )
        return {"decision": decision}

    @staticmethod
    def _record_decision(state: ApprovalState) -> dict[str, Any]:
        return {
            "status": (
                "approved" if state["decision"].get("approved") is True else "rejected"
            )
        }
