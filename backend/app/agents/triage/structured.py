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
                    "你是安全告警研判助手。只能使用输入中的证据，必须区分已观察事实、"
                    "推断和未知项。不得编造证据 ID；不执行任何处置动作。",
                ),
                (
                    "human",
                    "告警：\n{alert_json}\n\n证据：\n{evidence_json}\n\n"
                    "检索资料：\n{citations_json}\n\n{format_instructions}",
                ),
            ]
        )
        self._react_parser = PydanticOutputParser(pydantic_object=ReActToolDecision)
        self._react_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是安全调查动作规划器。只能从给定只读工具中选择一个动作。"
                    "禁止选择处置、写入、Shell、SQL 或通用 HTTP 工具。"
                    "query_network_context、query_endpoint_context、"
                    "query_identity_context 的 tool_input 必须且只能是 "
                    '{{"target":"告警实体或指标的原始值"}}；'
                    "search_security_knowledge 必须且只能是 "
                    '{{"query":"检索文本"}}；finish 的 tool_input 必须是空对象。'
                    "不得改写、推测或新造 target。"
                    "decision_summary 只记录可审计的选择依据，不输出隐藏思维链。",
                ),
                (
                    "human",
                    "告警：\n{alert_json}\n\nCritic：\n{critic_json}\n\n"
                    "已调用工具：{attempted_tools}\n剩余迭代：{remaining_iterations}\n\n"
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
    ) -> dict[str, Any]:
        prompt_value = self._prompt.invoke(
            {
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
                "attempted_tools": ", ".join(attempted_tools) or "无",
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
