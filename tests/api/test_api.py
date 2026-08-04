import time

from app.adapters.mock_xdr import MockXDRAdapter
from app.agents.triage.graph import TriageGraphRunner
from app.api.dependencies import (
    get_auth_service,
    get_job_repository,
    get_job_runtime,
    get_triage_graph_runner,
)
from app.core.config import Settings, get_settings
from app.main import app
from app.observability.langsmith import LangSmithTracePolicy
from app.rag.hybrid import HybridRetriever
from app.runtime.jobs import JobRepository, JobRuntime
from app.services.auth import AuthRepository, AuthService
from fastapi.testclient import TestClient

test_settings = Settings(_env_file=None, langsmith_tracing=False)
test_repository = JobRepository()
test_graph_runner = TriageGraphRunner(
    xdr=MockXDRAdapter(),
    retriever=HybridRetriever(),
    trace_policy=LangSmithTracePolicy(Settings(_env_file=None, langsmith_tracing=False)),
)
test_runtime = JobRuntime(test_repository, test_graph_runner)
test_auth = AuthService(AuthRepository(), session_hours=8)
test_auth.bootstrap_admin("test-admin", "correct-horse-battery-staple")
app.dependency_overrides[get_job_repository] = lambda: test_repository
app.dependency_overrides[get_triage_graph_runner] = lambda: test_graph_runner
app.dependency_overrides[get_job_runtime] = lambda: test_runtime
app.dependency_overrides[get_auth_service] = lambda: test_auth
app.dependency_overrides[get_settings] = lambda: test_settings

client = TestClient(app)
client.__enter__()
login_response = client.post(
    "/api/v1/auth/login",
    json={"username": "test-admin", "password": "correct-horse-battery-staple"},
)
assert login_response.status_code == 200
CSRF_HEADERS = {"X-CSRF-Token": client.cookies.get("ssa_csrf") or ""}


def wait_for_terminal(job_id: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        body = response.json()
        if body["status"] in {"SUCCEEDED", "DEGRADED", "FAILED"}:
            return body
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach a terminal state")


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
    headers = {**CSRF_HEADERS, "Idempotency-Key": "api-test-tri-001"}
    first = client.post("/api/v1/triage/jobs", json=payload, headers=headers)
    second = client.post("/api/v1/triage/jobs", json=payload, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]

    job_id = first.json()["job_id"]
    job_body = wait_for_terminal(job_id)
    result = client.get(f"/api/v1/jobs/{job_id}/result")

    assert job_body["status"] == "SUCCEEDED"
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
    assert job_body["analysis_version"] == 1
    assert job_body["workflow_version_id"] == "workflow_default_v1"

    trace = client.get(f"/api/v1/traces/{first.json()['trace_id']}")
    assert trace.status_code == 200
    trace_body = trace.json()
    assert trace_body["checkpoint_count"] >= 7
    assert trace_body["checkpoints"][-1]["last_node"] == "policy_finalize"
    assert "evidence" not in trace_body["checkpoints"][-1]

    events = client.get(f"/api/v1/jobs/{job_id}/events")
    assert events.status_code == 200
    event_types = [item["event_type"] for item in events.json()]
    assert event_types[0] == "job_created"
    assert event_types[-2:] == ["result_saved", "job_status_changed"]
    assert event_types.count("agent_step_completed") == 4
    assert {
        "node_completed",
        "plan_created",
        "agent_completed",
        "tool_completed",
        "critic_completed",
        "policy_finalized",
    } <= set(event_types)


def test_same_alert_can_be_analyzed_again_and_rerun_is_versioned() -> None:
    payload = {
        "alert_ref": {"source": "mock-xdr", "external_id": "A-TRI-002"},
        "mode": "mock",
    }
    first = client.post("/api/v1/triage/jobs", json=payload, headers=CSRF_HEADERS)
    second = client.post("/api/v1/triage/jobs", json=payload, headers=CSRF_HEADERS)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] != second.json()["job_id"]

    rerun = client.post(
        f"/api/v1/jobs/{first.json()['job_id']}/rerun",
        headers=CSRF_HEADERS,
    )
    assert rerun.status_code == 202
    assert rerun.json()["case_id"] == first.json()["case_id"]
    assert rerun.json()["parent_job_id"] == first.json()["job_id"]
    assert rerun.json()["analysis_version"] == 2


def test_idempotency_key_cannot_be_reused_for_different_request() -> None:
    headers = {**CSRF_HEADERS, "Idempotency-Key": "api-test-conflict"}
    first = client.post(
        "/api/v1/triage/jobs",
        json={"alert_ref": {"source": "mock-xdr", "external_id": "A-TRI-001"}},
        headers=headers,
    )
    conflict = client.post(
        "/api/v1/triage/jobs",
        json={"alert_ref": {"source": "mock-xdr", "external_id": "A-TRI-003"}},
        headers=headers,
    )
    assert first.status_code == 202
    assert conflict.status_code == 409


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


def test_auth_me_and_csrf_gate() -> None:
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "admin"

    denied = client.post(
        "/api/v1/triage/jobs",
        json={"alert_ref": {"source": "mock-xdr", "external_id": "A-TRI-001"}},
    )
    assert denied.status_code == 403


def test_sse_stream_contains_auditable_node_and_terminal_events() -> None:
    accepted = client.post(
        "/api/v1/triage/jobs",
        json={"alert_ref": {"source": "mock-xdr", "external_id": "A-TRI-001"}},
        headers=CSRF_HEADERS,
    )
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    wait_for_terminal(job_id)

    with client.stream("GET", f"/api/v1/jobs/{job_id}/stream") as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: plan_created" in body
    assert "event: agent_step_completed" in body
    assert "event: policy_finalized" in body
    assert "event: job_status_changed" in body
