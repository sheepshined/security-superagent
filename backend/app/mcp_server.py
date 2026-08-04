from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from app.adapters.mock_xdr import MockXDRAdapter, ScenarioNotFoundError
from app.domain.models import AlertRef
from app.rag.hybrid import HybridRetriever
from app.runtime.jobs import JobNotFoundError, JobRepository, ResultNotReadyError


class SecurityMCPTools:
    """Read-only, minimized views exposed through the MCP boundary."""

    def __init__(
        self,
        *,
        retriever: HybridRetriever,
        xdr: MockXDRAdapter,
        jobs: JobRepository,
    ) -> None:
        self._retriever = retriever
        self._xdr = xdr
        self._jobs = jobs

    def knowledge_search(self, query: str, top_k: int = 4) -> dict[str, Any]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query is required")
        bounded_top_k = max(1, min(top_k, 8))
        hits = self._retriever.search(
            normalized_query[:4000],
            top_k=bounded_top_k,
            allowed_access_labels={"public", "synthetic"},
        )
        return {
            "query": normalized_query[:4000],
            "results": [
                {
                    "document_id": hit.chunk.document_id,
                    "chunk_id": hit.chunk.chunk_id,
                    "title": hit.chunk.title,
                    "source_uri": hit.chunk.source_uri,
                    "version": hit.chunk.version,
                    "excerpt": hit.chunk.content[:1200],
                    "score": round(hit.final_score, 6),
                }
                for hit in hits
            ],
        }

    def alert_context(self, external_id: str) -> dict[str, Any]:
        normalized_id = external_id.strip()
        if not normalized_id:
            raise ValueError("external_id is required")
        try:
            alert = self._xdr.get_alert(
                AlertRef(source="mock-xdr", external_id=normalized_id[:128])
            )
            evidence = self._xdr.get_evidence(alert)
        except ScenarioNotFoundError as exc:
            raise ValueError("synthetic alert not found") from exc
        return {
            "classification": "synthetic",
            "alert": alert.model_dump(mode="json"),
            "evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "type": item.type,
                    "source": item.source,
                    "statement": item.statement,
                    "observed_at": item.observed_at.isoformat(),
                    "confidence": item.confidence,
                }
                for item in evidence
            ],
        }

    def trace_summary(self, trace_id: str) -> dict[str, Any]:
        normalized_id = trace_id.strip()
        if not normalized_id:
            raise ValueError("trace_id is required")
        try:
            job = self._jobs.get_by_trace_id(normalized_id[:128])
        except JobNotFoundError as exc:
            raise ValueError("trace not found") from exc
        summary: dict[str, Any] = {
            "trace_id": job.trace_id,
            "job_id": job.job_id,
            "case_id": job.case_id,
            "status": job.status.value,
            "progress": job.progress,
            "current_node": job.current_node,
            "analysis_version": job.analysis_version,
            "workflow_version_id": job.workflow_version_id,
            "audit_event_count": self._jobs.audit_event_count(job.job_id),
        }
        try:
            result = self._jobs.get_result(job.job_id)
        except ResultNotReadyError:
            return summary
        summary["result"] = {
            "verdict": result.verdict.value,
            "risk_score": result.risk_score,
            "confidence": result.confidence,
            "model_health": result.model_health.value,
            "evidence_count": len(result.evidence),
            "citation_count": len(result.citations),
            "agent_count": len(result.agent_reports),
            "conflict_count": len(result.conflicts),
            "workflow_trace": result.workflow_trace,
        }
        return summary


def build_mcp_server(tools: SecurityMCPTools, *, host: str, port: int) -> FastMCP:
    server = FastMCP(
        "Security SuperAgent Read-Only Gateway",
        instructions=(
            "Read-only security investigation tools. The alert tool exposes synthetic mock "
            "data only. No response or mutation tools are available."
        ),
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
        streamable_http_path="/mcp",
    )

    @server.tool()
    def knowledge_search(query: str, top_k: int = 4) -> dict[str, Any]:
        """Search public/synthetic security knowledge with traceable source metadata."""

        return tools.knowledge_search(query, top_k)

    @server.tool()
    def alert_context(external_id: str) -> dict[str, Any]:
        """Return normalized context for a synthetic mock XDR alert."""

        return tools.alert_context(external_id)

    @server.tool()
    def trace_summary(trace_id: str) -> dict[str, Any]:
        """Return a minimized audit summary without hidden reasoning or raw secrets."""

        return tools.trace_summary(trace_id)

    return server


def main() -> None:
    from app.api.dependencies import get_job_repository, get_retriever, get_xdr_adapter
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.mcp_enabled:
        raise RuntimeError("MCP gateway is disabled")
    tools = SecurityMCPTools(
        retriever=get_retriever(),
        xdr=get_xdr_adapter(),
        jobs=get_job_repository(),
    )
    server = build_mcp_server(tools, host=settings.mcp_host, port=settings.mcp_port)
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
