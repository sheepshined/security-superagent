from dataclasses import dataclass, field

from pydantic import Field, ValidationError

from app.adapters.protocols import XDRAdapter
from app.agents.triage.tools import create_knowledge_search_tool
from app.domain.models import Citation, Evidence, NormalizedAlert, StrictModel
from app.rag.hybrid import HybridRetriever
from app.tools.registry import DEFAULT_TOOL_REGISTRY


class InvestigationToolDeniedError(PermissionError):
    pass


class InvestigationTargetInput(StrictModel):
    target: str = Field(min_length=1, max_length=2048)


class InvestigationKnowledgeInput(StrictModel):
    query: str = Field(default="", max_length=12000)


@dataclass(slots=True)
class InvestigationToolResult:
    evidence: list[Evidence] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    summary: str = ""


class ReadOnlyInvestigationToolRegistry:
    """Hard allowlist for evidence gathering; no response/write tools are registered."""

    ALLOWED_TOOLS = DEFAULT_TOOL_REGISTRY.read_only_names

    def __init__(self, xdr: XDRAdapter, retriever: HybridRetriever) -> None:
        self._xdr = xdr
        self._knowledge_tool = create_knowledge_search_tool(retriever)

    def invoke(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, str],
        alert: NormalizedAlert,
    ) -> InvestigationToolResult:
        if tool_name not in self.ALLOWED_TOOLS:
            raise InvestigationToolDeniedError(f"tool is not allowlisted: {tool_name}")
        if tool_name == "search_security_knowledge":
            try:
                knowledge_input = InvestigationKnowledgeInput.model_validate(tool_input)
            except ValidationError as exc:
                raise InvestigationToolDeniedError(
                    "knowledge tool input does not match the approved schema"
                ) from exc
            return self._search_knowledge(knowledge_input, alert)
        try:
            target_input = InvestigationTargetInput.model_validate(tool_input)
        except ValidationError as exc:
            raise InvestigationToolDeniedError(
                "investigation tool input does not match the approved target schema"
            ) from exc
        self._validate_target(target_input.target, alert)
        evidence = self._xdr.get_evidence(alert)
        allowed_types = {
            "query_network_context": {"network_flow"},
            "query_endpoint_context": {
                "process_tree",
                "file_event",
                "endpoint_event",
                "telemetry_gap",
                "asset_record",
                "change_record",
            },
            "query_identity_context": {"identity_event"},
        }[tool_name]
        matched = [item for item in evidence if item.type in allowed_types]
        return InvestigationToolResult(
            evidence=matched,
            summary=(
                f"{tool_name} 返回 {len(matched)} 条只读证据，"
                f"类型为 {', '.join(sorted({item.type for item in matched})) or '无'}。"
            ),
        )

    def _search_knowledge(
        self,
        tool_input: InvestigationKnowledgeInput,
        alert: NormalizedAlert,
    ) -> InvestigationToolResult:
        query = tool_input.query.strip()
        if not query:
            query = " ".join([alert.title, *alert.labels])
        hits = self._knowledge_tool.invoke({"query": query[:12000], "top_k": 4})
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
        preferred_documents = self._preferred_documents(alert)
        if preferred_documents:
            rank = {document_id: index for index, document_id in enumerate(preferred_documents)}
            citations.sort(key=lambda item: rank.get(item.document_id, len(rank)))
        return InvestigationToolResult(
            citations=citations,
            summary=f"知识检索返回 {len(citations)} 条满足阈值的可追溯引用。",
        )

    @staticmethod
    def _preferred_documents(alert: NormalizedAlert) -> list[str]:
        """Apply deterministic scenario-aware reranking after hybrid retrieval."""
        labels = set(alert.labels)
        if "missing-endpoint-evidence" in labels:
            return [
                "playbook-lateral-movement-response",
                "mitre-attack-t1078",
            ]
        if "evidence-conflict" in labels:
            return [
                "mitre-attack-t1059-001",
                "mitre-attack-t1078",
                "cn-cii-security-regulation-operations",
                "playbook-lateral-movement-response",
            ]
        if "candidate-false-positive" in labels:
            return [
                "playbook-lateral-movement-response",
                "mitre-attack-t1021-002",
            ]
        if "lateral-movement" in labels:
            return [
                "mitre-attack-t1021-002",
                "mitre-attack-t1059-001",
                "mitre-attack-t1078",
                "playbook-lateral-movement-response",
            ]
        return []

    @staticmethod
    def _validate_target(target: str, alert: NormalizedAlert) -> None:
        allowed_targets = {
            *(entity.id for entity in alert.entities),
            *(indicator.value for indicator in alert.indicators),
        }
        if not target or target not in allowed_targets:
            raise InvestigationToolDeniedError(
                "tool target must be an entity or indicator from the normalized alert"
            )
