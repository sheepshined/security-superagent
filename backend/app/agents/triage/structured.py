import json
from typing import Any, Literal

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import Field

from app.adapters.models import (
    DataClassification,
    ModelAdapter,
    ModelMessage,
    ModelRequest,
)
from app.domain.enums import Verdict
from app.domain.models import (
    Citation,
    CriticReport,
    Evidence,
    NormalizedAlert,
    StrictModel,
)


class LLMTriageAssessment(StrictModel):
    verdict: Verdict
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=2000)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class ReActToolDecision(StrictModel):
    tool_name: Literal[
        "query_network_context",
        "query_endpoint_context",
        "query_identity_context",
        "search_security_knowledge",
        "finish",
    ]
    tool_input: dict[str, str] = Field(default_factory=dict)
    decision_summary: str = Field(min_length=1, max_length=1000)


class StructuredTriageAnalyzer:
    """LangChain prompt/parser boundary around the configured model adapter."""

    def __init__(self, model: ModelAdapter) -> None:
        self._model = model
        self._parser = PydanticOutputParser(pydantic_object=LLMTriageAssessment)
        self._prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a security-alert assessment assistant. Use only the supplied "
                    "evidence. Distinguish observed facts, inferences, and unknowns. Never "
                    "invent evidence IDs and never execute a response action. Return the "
                    "requested structured assessment, not hidden chain-of-thought.",
                ),
                (
                    "human",
                    "Analyst investigation goal:\n{analyst_query}\n\n"
                    "Alert:\n{alert_json}\n\nEvidence:\n{evidence_json}\n\n"
                    "Retrieved sources:\n{citations_json}\n\n{format_instructions}",
                ),
            ]
        )
        self._react_parser = PydanticOutputParser(pydantic_object=ReActToolDecision)
        self._react_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a security investigation action selector. Choose at most one "
                    "action from the supplied read-only tools. Never select response, write, "
                    "shell, SQL, or generic HTTP tools. query_network_context, "
                    "query_endpoint_context, and query_identity_context accept only "
                    '{{"target":"an exact alert entity or indicator"}}. '
                    "search_security_knowledge accepts only "
                    '{{"query":"retrieval text"}}; finish accepts an empty object. '
                    "Do not transform or invent targets. decision_summary must contain only "
                    "an auditable selection basis, not hidden chain-of-thought.",
                ),
                (
                    "human",
                    "Alert:\n{alert_json}\n\nCritic:\n{critic_json}\n\n"
                    "Attempted tools: {attempted_tools}\n"
                    "Remaining iterations: {remaining_iterations}\n\n"
                    "{format_instructions}",
                ),
            ]
        )

    def analyze(
        self,
        *,
        alert: NormalizedAlert,
        evidence: list[Evidence],
        citations: list[Citation],
        data_classification: DataClassification,
        analyst_query: str = "",
    ) -> dict[str, Any]:
        prompt_value = self._prompt.invoke(
            {
                "analyst_query": (
                    analyst_query or "Assess the alert using only the supplied evidence."
                ),
                "alert_json": alert.model_dump_json(indent=2),
                "evidence_json": json.dumps(
                    [item.model_dump(mode="json") for item in evidence],
                    ensure_ascii=False,
                    indent=2,
                ),
                "citations_json": json.dumps(
                    [item.model_dump(mode="json") for item in citations],
                    ensure_ascii=False,
                    indent=2,
                ),
                "format_instructions": self._parser.get_format_instructions(),
            }
        )
        messages = [
            ModelMessage(
                role=self._map_role(message.type),
                content=str(message.content),
            )
            for message in prompt_value.to_messages()
        ]
        response = self._model.complete(
            ModelRequest(
                messages=messages,
                data_classification=data_classification,
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=1600,
            )
        )
        assessment = self._parser.parse(response.content)
        return {
            **assessment.model_dump(mode="json"),
            "provider": response.provider,
            "model": response.model,
        }

    def select_react_action(
        self,
        *,
        alert: NormalizedAlert,
        critic: CriticReport,
        attempted_tools: list[str],
        remaining_iterations: int,
        data_classification: DataClassification,
    ) -> ReActToolDecision:
        prompt_value = self._react_prompt.invoke(
            {
                "alert_json": alert.model_dump_json(indent=2),
                "critic_json": critic.model_dump_json(indent=2),
                "attempted_tools": ", ".join(attempted_tools) or "none",
                "remaining_iterations": remaining_iterations,
                "format_instructions": self._react_parser.get_format_instructions(),
            }
        )
        response = self._model.complete(
            ModelRequest(
                messages=[
                    ModelMessage(
                        role=self._map_role(message.type),
                        content=str(message.content),
                    )
                    for message in prompt_value.to_messages()
                ],
                data_classification=data_classification,
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=800,
            )
        )
        return self._react_parser.parse(response.content)

    @staticmethod
    def _map_role(message_type: str) -> str:
        return {
            "human": "user",
            "ai": "assistant",
            "system": "system",
            "tool": "tool",
        }[message_type]
