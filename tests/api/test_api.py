from app.adapters.mock_xdr import MockXDRAdapter
from app.agents.triage.graph import TriageGraphRunner
from app.api.dependencies import (
    get_job_repository,
    get_job_runtime,
    get_triage_graph_runner,
)
from app.core.config import Settings, get_settings
from app.main import app
from app.observability.langsmith import LangSmithTracePolicy
from app.rag.hybrid import HybridRetriever
from app.runtime.jobs import JobRepository, JobRuntime
from fastapi.testclient import TestClient

test_settings = Settings(_env_file=None, langsmith_tracing=False)
test_repository = JobRepository()
test_graph_runner = TriageGraphRunner(
    xdr=MockXDRAdapter(),
    retriever=HybridRetriever(),
    trace_policy=LangSmithTracePolicy(Settings(_env_file=None, langsmith_tracing=False)),
)
test_runtime = JobRuntime(test_repository, test_graph_runner)
app.dependency_overrides[get_job_repository] = lambda: test_repository
app.dependency_overrides[get_triage_graph_runner] = lambda: test_graph_runner
app.dependency_overrides[get_job_runtime] = lambda: test_runtime
app.dependency_overrides[get_settings] = lambda: test_settings

client = TestClient(app)


def test_health_and_readiness() -> None:
    assert client.get("/healthz").status_code == 200
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["dependencies"]["scenario_count"] == 3
    assert ready.json()["dependencies"]["sqlite"] == "ready"
    assert ready.json()["dependencies"]["model"] == "mock"
    assert ready.json()["dependencies"]["langsmith"] == "disabled"


def test_triage_job_is_idempotent_and_returns_result() -> None:
    payload = {
        "alert_ref": {"source": "mock-xdr", "external_id": "A-TRI-001"},
        "mode": "mock",
        "options": {"allow_superagent_escalation": True},
    }
    first = client.post("/api/v1/triage/jobs", json=payload)
    second = client.post("/api/v1/triage/jobs", json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]

    job_id = first.json()["job_id"]
    job = client.get(f"/api/v1/jobs/{job_id}")
    result = client.get(f"/api/v1/jobs/{job_id}/result")

    assert job.json()["status"] == "SUCCEEDED"
    assert result.status_code == 200
    assert result.json()["verdict"] == "true_positive"
    assert result.json()["citations"]
    assert result.json()["workflow_trace"] == [
        "load_alert",
        "plan_investigation",
        "execute_initial_plan",
        "retrieve_knowledge",
        "structured_assessment",
        "critic_verify",
        "policy_finalize",
    ]
    assert result.json()["investigation"]["stop_reason"] == "not_required"

    trace = client.get(f"/api/v1/traces/{first.json()['trace_id']}")
    assert trace.status_code == 200
    trace_body = trace.json()
    assert trace_body["checkpoint_count"] >= 7
    assert trace_body["checkpoints"][-1]["last_node"] == "policy_finalize"
    assert "evidence" not in trace_body["checkpoints"][-1]

    events = client.get(f"/api/v1/jobs/{job_id}/events")
    assert events.status_code == 200
    assert [item["event_type"] for item in events.json()] == [
        "job_created",
        "job_status_changed",
        "result_saved",
        "job_status_changed",
    ]


def test_knowledge_search_returns_traceable_source() -> None:
    response = client.get(
        "/api/v1/knowledge/search",
        params={"q": "SMB 横向移动 PowerShell", "top_k": 3},
    )

    assert response.status_code == 200
    assert response.json()
    assert any(
        item["chunk"]["source_uri"].startswith("https://attack.mitre.org/")
        for item in response.json()
    )
