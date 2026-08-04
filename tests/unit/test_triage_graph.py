import json
from pathlib import Path

import pytest
from app.adapters.mock_xdr import MockXDRAdapter
from app.adapters.models import DataClassification, ModelAdapter, ModelRequest, ModelResponse
from app.agents.approval.graph import ApprovalGraphRunner
from app.agents.triage.graph import TriageGraphRunner
from app.agents.triage.investigation_tools import (
    InvestigationToolDeniedError,
    ReadOnlyInvestigationToolRegistry,
)
from app.agents.triage.structured import StructuredTriageAnalyzer
from app.core.config import Settings
from app.domain.enums import Verdict
from app.domain.models import AlertRef, Evidence, NormalizedAlert
from app.observability.langsmith import LangSmithTracePolicy
from app.persistence.checkpoints import create_sqlite_checkpointer
from app.rag.hybrid import HybridRetriever


class GroundedModelStub:
    def complete(self, request: ModelRequest) -> ModelResponse:
        assert request.response_format == {"type": "json_object"}
        return ModelResponse(
            content=json.dumps(
                {
                    "verdict": "true_positive",
                    "confidence": 0.91,
                    "rationale": "多源证据支持横向移动。",
                    "supporting_evidence_ids": [
                        "ev_tri_001_network",
                        "ev_tri_001_process",
                        "ev_tri_001_identity",
                    ],
                    "unknowns": [],
                },
                ensure_ascii=False,
            ),
            model="deepseek-test",
            provider="test",
        )


class AliasToolInputModelStub:
    def __init__(self) -> None:
        self.react_calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.max_tokens == 800:
            self.react_calls += 1
            tool_name = (
                "query_endpoint_context"
                if self.react_calls == 1
                else "query_identity_context"
            )
            tool_input = (
                {"target": "asset_ws_041"}
                if self.react_calls == 1
                else {"host_id": "asset_ws_041"}
            )
            content = json.dumps(
                {
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "decision_summary": "补充缺失的只读证据。",
                },
                ensure_ascii=False,
            )
        else:
            content = json.dumps(
                {
                    "verdict": "needs_review",
                    "confidence": 0.6,
                    "rationale": "端点与身份数据不完整，需要人工复核。",
                    "supporting_evidence_ids": [
                        "ev_tri_003_network",
                        "ev_tri_003_edr_gap",
                    ],
                    "unknowns": ["缺少进程树", "缺少身份事件"],
                },
                ensure_ascii=False,
            )
        return ModelResponse(content=content, model="deepseek-test", provider="test")


class RecoveringXDRAdapter(MockXDRAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.evidence_queries = 0

    def get_evidence(self, alert: NormalizedAlert) -> list[Evidence]:
        self.evidence_queries += 1
        evidence = super().get_evidence(alert)
        if self.evidence_queries >= 2:
            evidence.append(
                Evidence(
                    evidence_id="ev_recovered_process",
                    type="process_tree",
                    source="mock-edr-recovery",
                    statement="EDR 恢复后取得浏览器进程发起网络连接的进程树。",
                    observed_at=alert.occurred_at,
                    confidence=0.87,
                    raw_ref="mock://recovery/process",
                    integrity_hash="a" * 64,
                )
            )
        if self.evidence_queries >= 3:
            evidence.append(
                Evidence(
                    evidence_id="ev_recovered_identity",
                    type="identity_event",
                    source="mock-identity-recovery",
                    statement="补充取得当前终端用户的认证上下文。",
                    observed_at=alert.occurred_at,
                    confidence=0.84,
                    raw_ref="mock://recovery/identity",
                    integrity_hash="b" * 64,
                )
            )
        return evidence


def make_runner(
    *,
    with_model: bool = False,
    model: ModelAdapter | None = None,
    xdr: MockXDRAdapter | None = None,
) -> TriageGraphRunner:
    selected_model = model or (GroundedModelStub() if with_model else None)
    analyzer = StructuredTriageAnalyzer(selected_model) if selected_model else None
    settings = Settings(
        _env_file=None,
        langsmith_tracing=False,
        langsmith_api_key="",
    )
    return TriageGraphRunner(
        xdr=xdr or MockXDRAdapter(),
        retriever=HybridRetriever(),
        trace_policy=LangSmithTracePolicy(settings),
        analyzer=analyzer,
    )


def test_triage_graph_runs_all_nodes_and_persists_checkpoint() -> None:
    runner = make_runner()

    result = runner.run(
        job_id="job_graph",
        case_id="case_graph",
        trace_id="trace_graph",
        alert_ref=AlertRef(source="mock-xdr", external_id="A-TRI-001"),
        data_classification=DataClassification.SYNTHETIC,
    )

    assert result.verdict == Verdict.TRUE_POSITIVE
    assert result.workflow_trace == [
        "load_alert",
        "plan_investigation",
        "execute_initial_plan",
        "retrieve_knowledge",
        "structured_assessment",
        "critic_verify",
        "policy_finalize",
    ]
    assert result.investigation is not None
    assert result.investigation.stop_reason == "not_required"
    checkpoint = runner.checkpoint("trace_graph")
    assert checkpoint["result"].job_id == "job_graph"


def test_model_assessment_is_structured_grounded_and_policy_checked() -> None:
    runner = make_runner(with_model=True)

    result = runner.run(
        job_id="job_model",
        case_id="case_model",
        trace_id="trace_model",
        alert_ref=AlertRef(source="mock-xdr", external_id="A-TRI-001"),
        data_classification=DataClassification.SYNTHETIC,
    )

    assert result.model_assessment is not None
    assert result.model_assessment["provider"] == "test"
    assert result.model_assessment["agrees_with_policy"] is True
    assert result.model_assessment["evidence_grounded"] is True
    assert result.model_assessment["decision_authority"] == "deterministic_policy"


def test_missing_evidence_enters_bounded_react_and_stops_safely() -> None:
    runner = make_runner()

    result = runner.run(
        job_id="job_react",
        case_id="case_react",
        trace_id="trace_react",
        alert_ref=AlertRef(source="mock-xdr", external_id="A-TRI-003"),
        data_classification=DataClassification.SYNTHETIC,
    )

    assert result.verdict == Verdict.NEEDS_REVIEW
    assert result.investigation is not None
    assert result.investigation.replans == 1
    assert result.investigation.tool_calls <= 6
    assert len(result.investigation.react_steps) == 2
    assert result.investigation.stop_reason == "iteration_limit"
    assert {
        step.tool_name for step in result.investigation.react_steps
    } <= ReadOnlyInvestigationToolRegistry.ALLOWED_TOOLS
    assert "bounded_react" in result.workflow_trace


def test_bounded_react_normalizes_safe_model_target_alias() -> None:
    model = AliasToolInputModelStub()
    runner = make_runner(model=model)

    result = runner.run(
        job_id="job_react_alias",
        case_id="case_react_alias",
        trace_id="trace_react_alias",
        alert_ref=AlertRef(source="mock-xdr", external_id="A-TRI-003"),
        data_classification=DataClassification.SYNTHETIC,
    )

    assert result.investigation is not None
    second_step = result.investigation.react_steps[1]
    assert second_step.tool_name == "query_identity_context"
    assert second_step.tool_input == {"target": "asset_ws_041"}
    assert second_step.status == "completed"
    assert result.investigation.stop_reason == "iteration_limit"
    assert model.react_calls == 2


def test_bounded_react_can_acquire_late_evidence() -> None:
    runner = make_runner(xdr=RecoveringXDRAdapter())

    result = runner.run(
        job_id="job_recovery",
        case_id="case_recovery",
        trace_id="trace_recovery",
        alert_ref=AlertRef(source="mock-xdr", external_id="A-TRI-003"),
        data_classification=DataClassification.SYNTHETIC,
    )

    assert result.investigation is not None
    assert result.investigation.stop_reason == "evidence_acquired"
    assert result.investigation.critic.evidence_sufficient is True
    assert {"ev_recovered_process", "ev_recovered_identity"} <= {
        item.evidence_id for item in result.evidence
    }


def test_investigation_registry_denies_write_tool() -> None:
    xdr = MockXDRAdapter()
    registry = ReadOnlyInvestigationToolRegistry(xdr, HybridRetriever())
    alert = xdr.get_alert(AlertRef(source="mock-xdr", external_id="A-TRI-001"))

    with pytest.raises(InvestigationToolDeniedError):
        registry.invoke(
            tool_name="edr.isolate_host",
            tool_input={"target": "asset_fin_023"},
            alert=alert,
        )


def test_investigation_registry_rejects_unapproved_target_alias_at_boundary() -> None:
    xdr = MockXDRAdapter()
    registry = ReadOnlyInvestigationToolRegistry(xdr, HybridRetriever())
    alert = xdr.get_alert(AlertRef(source="mock-xdr", external_id="A-TRI-003"))

    with pytest.raises(InvestigationToolDeniedError):
        registry.invoke(
            tool_name="query_identity_context",
            tool_input={"host_id": "asset_ws_041"},
            alert=alert,
        )


def test_high_risk_approval_graph_interrupts_and_resumes() -> None:
    runner = ApprovalGraphRunner()
    thread_id = "approval_test"

    waiting = runner.request(
        thread_id=thread_id,
        action={"tool_name": "edr.isolate_host", "target": "host-001"},
    )

    assert waiting["__interrupt__"]
    completed = runner.resume(
        thread_id=thread_id,
        approved=True,
        reviewer="security-lead",
        comment="approved for synthetic exercise",
    )
    assert completed["status"] == "approved"
    assert completed["decision"]["reviewer"] == "security-lead"


def test_approval_interrupt_resumes_after_runner_recreation(tmp_path: Path) -> None:
    database_path = tmp_path / "approval-checkpoints.sqlite3"
    waiting_runner = ApprovalGraphRunner(
        checkpointer=create_sqlite_checkpointer(str(database_path))
    )
    waiting = waiting_runner.request(
        thread_id="approval:persistent",
        action={"tool_name": "edr.isolate_host", "target": "host-001"},
    )
    assert waiting["__interrupt__"]
    waiting_runner.close()

    restored_runner = ApprovalGraphRunner(
        checkpointer=create_sqlite_checkpointer(str(database_path))
    )
    completed = restored_runner.resume(
        thread_id="approval:persistent",
        approved=False,
        reviewer="security-lead",
        comment="target ownership needs confirmation",
    )
    restored_runner.close()

    assert completed["status"] == "rejected"
    assert completed["decision"]["reviewer"] == "security-lead"


def test_langsmith_policy_denies_real_data_by_default() -> None:
    settings = Settings(
        _env_file=None,
        langsmith_tracing=True,
        langsmith_api_key="test-key",
        langsmith_allow_real_security_data=False,
    )
    policy = LangSmithTracePolicy(settings)

    assert policy.is_allowed(DataClassification.SYNTHETIC) is True
    assert policy.is_allowed(DataClassification.REAL_SECURITY_DATA) is False


def test_sqlite_checkpoint_survives_runner_recreation(tmp_path: Path) -> None:
    database_path = tmp_path / "checkpoints.sqlite3"
    settings = Settings(_env_file=None, langsmith_tracing=False)
    trace_policy = LangSmithTracePolicy(settings)
    runner = TriageGraphRunner(
        xdr=MockXDRAdapter(),
        retriever=HybridRetriever(),
        trace_policy=trace_policy,
        checkpointer=create_sqlite_checkpointer(str(database_path)),
    )
    runner.run(
        job_id="job_persistent",
        case_id="case_persistent",
        trace_id="trace_persistent",
        alert_ref=AlertRef(source="mock-xdr", external_id="A-TRI-003"),
        data_classification=DataClassification.SYNTHETIC,
    )
    first_replay = runner.trace_replay("trace_persistent")
    runner.close()

    restored = TriageGraphRunner(
        xdr=MockXDRAdapter(),
        retriever=HybridRetriever(),
        trace_policy=trace_policy,
        checkpointer=create_sqlite_checkpointer(str(database_path)),
    )
    checkpoint = restored.checkpoint("trace_persistent")
    second_replay = restored.trace_replay("trace_persistent")
    restored.close()

    assert checkpoint["result"].job_id == "job_persistent"
    assert first_replay.checkpoint_count == second_replay.checkpoint_count
    assert second_replay.checkpoints[-1].last_node == "policy_finalize"
    assert [item.created_at for item in second_replay.checkpoints] == sorted(
        item.created_at for item in second_replay.checkpoints
    )
