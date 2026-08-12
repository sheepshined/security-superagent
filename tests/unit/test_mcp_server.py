import asyncio
from pathlib import Path

import pytest
from app.adapters.mock_xdr import MockXDRAdapter
from app.domain.models import AlertRef, TriageJobRequest
from app.mcp_server import SecurityMCPTools, build_mcp_server
from app.rag.hybrid import HybridRetriever
from app.runtime.jobs import JobRepository


def make_tools(tmp_path: Path) -> SecurityMCPTools:
    database_url = f"sqlite:///{(tmp_path / 'mcp.sqlite3').as_posix()}"
    return SecurityMCPTools(
        retriever=HybridRetriever(),
        xdr=MockXDRAdapter(),
        jobs=JobRepository(database_url),
    )


def test_mcp_exposes_only_three_read_only_tools(tmp_path: Path) -> None:
    server = build_mcp_server(make_tools(tmp_path), host="127.0.0.1", port=8100)

    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == {
        "knowledge_search",
        "alert_context",
        "trace_summary",
    }
    assert not any("execute" in tool.name or "isolate" in tool.name for tool in tools)


def test_mcp_alert_context_is_explicitly_synthetic(tmp_path: Path) -> None:
    payload = make_tools(tmp_path).alert_context("A-SUP-001")

    assert payload["classification"] == "synthetic"
    assert payload["alert"]["external_id"] == "A-SUP-001"
    assert len(payload["evidence"]) == 4


def test_mcp_knowledge_search_is_source_traceable(tmp_path: Path) -> None:
    payload = make_tools(tmp_path).knowledge_search("T1021.002 SMB lateral movement", 3)

    assert payload["results"]
    assert all(item["source_uri"].startswith("https://") for item in payload["results"])
    assert all(item["document_id"] and item["chunk_id"] for item in payload["results"])


def test_mcp_trace_summary_minimizes_data(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'trace.sqlite3').as_posix()}"
    repository = JobRepository(database_url)
    job, _ = repository.create_or_get(
        TriageJobRequest(
            alert_ref=AlertRef(source="mock-xdr", external_id="A-SUP-001")
        )
    )
    tools = SecurityMCPTools(
        retriever=HybridRetriever(),
        xdr=MockXDRAdapter(),
        jobs=repository,
    )

    summary = tools.trace_summary(job.trace_id)

    assert summary["trace_id"] == job.trace_id
    assert summary["audit_event_count"] == 1
    assert "request" not in summary
    assert "evidence" not in summary


def test_mcp_rejects_unknown_or_empty_inputs(tmp_path: Path) -> None:
    tools = make_tools(tmp_path)

    with pytest.raises(ValueError, match="query is required"):
        tools.knowledge_search("  ")
    with pytest.raises(ValueError, match="alert not found on the configured XDR source"):
        tools.alert_context("A-REAL-001")
    with pytest.raises(ValueError, match="trace not found"):
        tools.trace_summary("tr_missing")
