import tempfile
import time
from pathlib import Path
from uuid import uuid4

from app.adapters.mock_xdr import MockXDRAdapter
from app.adapters.response import MockResponseAdapter
from app.agents.triage.graph import TriageGraphRunner
from app.api.dependencies import (
    get_action_service,
    get_auth_service,
    get_job_repository,
    get_job_runtime,
    get_triage_graph_runner,
    get_workflow_service,
)
from app.core.config import Settings, get_settings
from app.main import app
from app.observability.langsmith import LangSmithTracePolicy
from app.rag.catalog import KnowledgeCatalog
from app.rag.hybrid import HybridRetriever
from app.runtime.jobs import JobRepository, JobRuntime
from app.services.actions import ActionRepository, ActionService
from app.services.auth import AuthRepository, AuthService
from app.services.workflows import WorkflowRepository, WorkflowService
from app.tools.registry import DEFAULT_TOOL_REGISTRY
from fastapi.testclient import TestClient

test_settings = Settings(_env_file=None, langsmith_tracing=False)
API_DATABASE_URL = (
    f"sqlite:///{(Path(tempfile.gettempdir()) / f'ssa-api-{uuid4().hex}.sqlite3').as_posix()}"
)
test_repository = JobRepository(API_DATABASE_URL)
test_graph_runner = TriageGraphRunner(
    xdr=MockXDRAdapter(),
    retriever=HybridRetriever(),
    trace_policy=LangSmithTracePolicy(Settings(_env_file=None, langsmith_tracing=False)),
)
test_runtime = JobRuntime(test_repository, test_graph_runner)
test_auth = AuthService(AuthRepository(API_DATABASE_URL), session_hours=8)
test_auth.bootstrap_admin("test-admin", "correct-horse-battery-staple")
test_actions = ActionService(
    ActionRepository(API_DATABASE_URL),
    test_repository,
    DEFAULT_TOOL_REGISTRY,
    MockResponseAdapter(),
)
test_workflows = WorkflowService(
    WorkflowRepository(API_DATABASE_URL),
    DEFAULT_TOOL_REGISTRY,
    KnowledgeCatalog(Path("knowledge/index"), Path("knowledge/sources.json")),
)
app.dependency_overrides[get_job_repository] = lambda: test_repository
app.dependency_overrides[get_triage_graph_runner] = lambda: test_graph_runner
app.dependency_overrides[get_job_runtime] = lambda: test_runtime
app.dependency_overrides[get_auth_service] = lambda: test_auth
app.dependency_overrides[get_action_service] = lambda: test_actions
app.dependency_overrides[get_workflow_service] = lambda: test_workflows
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
    assert ready.json()["dependencies"]["scenario_count"] == 5
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


def test_job_can_bind_only_an_active_immutable_workflow_version() -> None:
    template = client.get("/api/v1/workflows/templates").json()[0]
    created = client.post(
        f"/api/v1/workflows/{template['template_id']}/versions",
        json={"config": template["default_config"]},
        headers=CSRF_HEADERS,
    )
    assert created.status_code == 201
    workflow_version_id = created.json()["workflow_version_id"]

    draft_job = client.post(
        "/api/v1/triage/jobs",
        json={
            "alert_ref": {"source": "mock-xdr", "external_id": "A-TRI-002"},
            "options": {"workflow_version_id": workflow_version_id},
        },
        headers=CSRF_HEADERS,
    )
    assert draft_job.status_code == 422

    assert (
        client.post(
            f"/api/v1/workflows/versions/{workflow_version_id}/validate",
            headers=CSRF_HEADERS,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/workflows/versions/{workflow_version_id}/activate",
            headers=CSRF_HEADERS,
        ).status_code
        == 200
    )

    active_job = client.post(
        "/api/v1/triage/jobs",
        json={
            "alert_ref": {"source": "mock-xdr", "external_id": "A-TRI-002"},
            "options": {"workflow_version_id": workflow_version_id},
        },
        headers=CSRF_HEADERS,
    )
    assert active_job.status_code == 202
    assert active_job.json()["workflow_version_id"] == workflow_version_id

    missing_job = client.post(
        "/api/v1/triage/jobs",
        json={
            "alert_ref": {"source": "mock-xdr", "external_id": "A-TRI-002"},
            "options": {"workflow_version_id": "wfv_missing"},
        },
        headers=CSRF_HEADERS,
    )
    assert missing_job.status_code == 404


def test_response_action_api_enforces_four_eyes_and_full_audit_lifecycle() -> None:
    accepted = client.post(
        "/api/v1/triage/jobs",
        json={"alert_ref": {"source": "mock-xdr", "external_id": "A-SUP-001"}},
        headers=CSRF_HEADERS,
    )
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    assert wait_for_terminal(job_id)["status"] == "SUCCEEDED"

    proposed = client.post(
        "/api/v1/actions",
        json={"job_id": job_id, "proposal_index": 0},
        headers={**CSRF_HEADERS, "Idempotency-Key": "api-action-lifecycle"},
    )
    assert proposed.status_code == 201
    action_id = proposed.json()["action_id"]
    assert proposed.json()["status"] == "PROPOSED"

    dry_run = client.post(f"/api/v1/actions/{action_id}/dry-run", headers=CSRF_HEADERS)
    assert dry_run.status_code == 200
    assert dry_run.json()["status"] == "PENDING_APPROVAL"
    self_approval = client.post(
        f"/api/v1/actions/{action_id}/approve",
        json={"comment": "self approval must fail"},
        headers=CSRF_HEADERS,
    )
    assert self_approval.status_code == 403

    created_user = client.post(
        "/api/v1/admin/users",
        json={
            "username": "api-approver",
            "password": "correct-horse-approval-staple",
            "role": "approver",
        },
        headers=CSRF_HEADERS,
    )
    assert created_user.status_code == 201

    approver_client = TestClient(app)
    login = approver_client.post(
        "/api/v1/auth/login",
        json={
            "username": "api-approver",
            "password": "correct-horse-approval-staple",
        },
    )
    assert login.status_code == 200
    approver_csrf = {"X-CSRF-Token": approver_client.cookies.get("ssa_csrf") or ""}
    approved = approver_client.post(
        f"/api/v1/actions/{action_id}/approve",
        json={"comment": "independent approval"},
        headers=approver_csrf,
    )
    assert approved.json()["status"] == "APPROVED"
    executed = approver_client.post(
        f"/api/v1/actions/{action_id}/execute", headers=approver_csrf
    )
    assert executed.json()["status"] == "SUCCEEDED"
    verified = approver_client.post(
        f"/api/v1/actions/{action_id}/verify", headers=approver_csrf
    )
    assert verified.json()["status"] == "VERIFIED"
    rolled_back = approver_client.post(
        f"/api/v1/actions/{action_id}/rollback", headers=approver_csrf
    )
    assert rolled_back.json()["status"] == "ROLLED_BACK"

    assert client.get(f"/api/v1/actions/{action_id}").json()["status"] == "ROLLED_BACK"
    assert client.get("/api/v1/actions", params={"job_id": job_id}).json()
    events = client.get(f"/api/v1/actions/{action_id}/events")
    assert events.status_code == 200
    assert [item["event_type"] for item in events.json()][-3:] == [
        "action_execution_succeeded",
        "action_verified",
        "action_rolled_back",
    ]
    assert client.get("/api/v1/actions/act_missing").status_code == 404


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
