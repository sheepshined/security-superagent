from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import (
    FactType,
    JobStatus,
    ModelHealth,
    RunMode,
    Severity,
    ToolRisk,
    UserRole,
    Verdict,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


OpaqueId = Annotated[str, Field(min_length=1, max_length=128)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AlertRef(StrictModel):
    source: str = Field(min_length=1, max_length=64)
    external_id: OpaqueId


class TriageOptions(StrictModel):
    time_window_minutes: int = Field(default=60, ge=5, le=1440)
    allow_superagent_escalation: bool = True
    max_risk_level: Severity = Severity.HIGH
    workflow_version_id: str | None = Field(default=None, max_length=128)


class TriageJobRequest(StrictModel):
    alert_ref: AlertRef
    mode: RunMode = RunMode.MOCK
    options: TriageOptions = Field(default_factory=TriageOptions)


class Entity(StrictModel):
    type: Literal["host", "user", "ip", "process", "service"]
    id: OpaqueId
    role: str = Field(min_length=1, max_length=64)


class Indicator(StrictModel):
    type: Literal["ip", "domain", "url", "hash", "cve"]
    value: str = Field(min_length=1, max_length=2048)


class NormalizedAlert(StrictModel):
    alert_id: OpaqueId
    source: str = Field(min_length=1, max_length=64)
    external_id: OpaqueId
    title: str = Field(min_length=1, max_length=512)
    severity: Severity
    occurred_at: datetime
    ingested_at: datetime = Field(default_factory=utc_now)
    entities: list[Entity] = Field(default_factory=list, max_length=128)
    indicators: list[Indicator] = Field(default_factory=list, max_length=128)
    labels: list[str] = Field(default_factory=list, max_length=64)
    raw_ref: str = Field(min_length=1, max_length=512)
    schema_version: str = "1.0"

    @field_validator("occurred_at", "ingested_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamps must include a timezone")
        return value


class Evidence(StrictModel):
    evidence_id: OpaqueId
    type: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=4000)
    observed_at: datetime
    confidence: float = Field(ge=0, le=1)
    raw_ref: str = Field(min_length=1, max_length=512)
    integrity_hash: str = Field(min_length=8, max_length=128)
    access_label: str = Field(default="synthetic", min_length=1, max_length=64)


class Finding(StrictModel):
    finding_id: OpaqueId
    statement: str = Field(min_length=1, max_length=4000)
    fact_type: FactType
    evidence_ids: list[OpaqueId] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    limitations: list[str] = Field(default_factory=list)


class Citation(StrictModel):
    citation_id: OpaqueId
    document_id: OpaqueId
    chunk_id: OpaqueId
    title: str = Field(min_length=1, max_length=512)
    source_uri: str = Field(min_length=1, max_length=2048)
    version: str = Field(min_length=1, max_length=64)
    excerpt: str = Field(min_length=1, max_length=2000)


class ActionProposal(StrictModel):
    tool_name: str = Field(min_length=1, max_length=128)
    target: OpaqueId
    scope: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=2000)
    expected_effect: str = Field(min_length=1, max_length=2000)
    rollback: str = Field(min_length=1, max_length=2000)
    risk_level: ToolRisk
    dry_run: bool = True
    requires_approval: bool = True
    expires_at: datetime | None = None


class InvestigationPlanStep(StrictModel):
    step_id: OpaqueId
    agent: Literal[
        "network_agent",
        "endpoint_agent",
        "identity_agent",
        "knowledge_agent",
    ]
    objective: str = Field(min_length=1, max_length=1000)
    required_evidence_types: list[str] = Field(default_factory=list)
    read_only: bool = True


class InvestigationPlan(StrictModel):
    strategy: Literal["plan_execute_verify_replan"] = "plan_execute_verify_replan"
    objective: str = Field(min_length=1, max_length=2000)
    steps: list[InvestigationPlanStep] = Field(min_length=1, max_length=16)
    max_replans: int = Field(default=1, ge=0, le=2)
    max_react_iterations: int = Field(default=2, ge=1, le=6)
    max_tool_calls: int = Field(default=6, ge=1, le=12)


class PlanStepExecution(StrictModel):
    step_id: OpaqueId
    agent: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=128)
    status: Literal["completed", "failed", "denied"]
    evidence_ids: list[OpaqueId] = Field(default_factory=list)
    citation_ids: list[OpaqueId] = Field(default_factory=list)
    observation_summary: str = Field(min_length=1, max_length=2000)


class ReplanRequest(StrictModel):
    replan_id: OpaqueId
    reason: str = Field(min_length=1, max_length=2000)
    missing_evidence_types: list[str] = Field(default_factory=list)
    original_step_ids: list[OpaqueId] = Field(default_factory=list)
    revised_steps: list[InvestigationPlanStep] = Field(default_factory=list)
    added_step_ids: list[OpaqueId] = Field(default_factory=list)
    removed_step_ids: list[OpaqueId] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class CriticReport(StrictModel):
    evidence_sufficient: bool
    requires_react: bool
    missing_evidence_types: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=2000)


class ReActAuditStep(StrictModel):
    iteration: int = Field(ge=1, le=12)
    decision_summary: str = Field(min_length=1, max_length=1000)
    tool_name: str = Field(min_length=1, max_length=128)
    tool_input: dict[str, str] = Field(default_factory=dict)
    observation_summary: str = Field(min_length=1, max_length=2000)
    new_evidence_ids: list[OpaqueId] = Field(default_factory=list)
    status: Literal["completed", "denied", "failed"]


class InvestigationTrace(StrictModel):
    plan: InvestigationPlan
    plan_executions: list[PlanStepExecution] = Field(default_factory=list)
    critic: CriticReport
    react_steps: list[ReActAuditStep] = Field(default_factory=list, max_length=12)
    replans: int = Field(default=0, ge=0, le=2)
    tool_calls: int = Field(default=0, ge=0, le=12)
    replan_history: list[ReplanRequest] = Field(default_factory=list, max_length=2)
    stop_reason: Literal[
        "not_required",
        "evidence_acquired",
        "evidence_exhausted",
        "iteration_limit",
        "tool_call_limit",
        "tool_denied",
        "human_review_required",
    ]


class TriageResult(StrictModel):
    job_id: OpaqueId
    case_id: OpaqueId
    verdict: Verdict
    risk_score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1, max_length=4000)
    facts: list[Finding] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    recommended_actions: list[ActionProposal] = Field(default_factory=list)
    requires_superagent: bool = False
    workflow_trace: list[str] = Field(default_factory=list)
    model_assessment: dict[str, Any] | None = None
    investigation: InvestigationTrace | None = None
    model_health: ModelHealth = ModelHealth.NOT_CONFIGURED
    degraded_reason: str | None = Field(default=None, max_length=1000)
    agent_reports: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    replan_history: list[ReplanRequest] = Field(default_factory=list)
    completed_at: datetime = Field(default_factory=utc_now)


class JobAccepted(StrictModel):
    job_id: OpaqueId
    case_id: OpaqueId
    status: JobStatus
    request_id: OpaqueId
    trace_id: OpaqueId
    analysis_version: int = Field(default=1, ge=1)
    parent_job_id: str | None = None
    workflow_version_id: str = Field(default="workflow_default_v1", min_length=1)


class JobView(JobAccepted):
    created_at: datetime
    updated_at: datetime
    progress: int = Field(ge=0, le=100)
    current_node: str | None = None
    error_code: str | None = None


class JobAuditEvent(StrictModel):
    event_id: int = Field(ge=1)
    job_id: OpaqueId
    event_type: str = Field(min_length=1, max_length=128)
    status: JobStatus
    current_node: str | None = Field(default=None, max_length=128)
    occurred_at: datetime
    data: dict[str, Any] = Field(default_factory=dict)


class TraceCheckpoint(StrictModel):
    checkpoint_id: OpaqueId
    trace_id: OpaqueId
    created_at: datetime
    step: int = Field(ge=-1)
    source: str = Field(min_length=1, max_length=64)
    last_node: str | None = Field(default=None, max_length=128)
    next_nodes: list[str] = Field(default_factory=list)
    evidence_count: int = Field(default=0, ge=0)
    citation_count: int = Field(default=0, ge=0)
    react_tool_calls: int = Field(default=0, ge=0)
    verdict: Verdict | None = None


class TraceReplay(StrictModel):
    trace_id: OpaqueId
    checkpoint_count: int = Field(ge=0)
    checkpoints: list[TraceCheckpoint] = Field(default_factory=list)


class ScenarioSummary(StrictModel):
    scenario_id: OpaqueId
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(min_length=1, max_length=2000)
    severity: Severity
    expected_verdict: Verdict
    supports_superagent: bool = False


class ErrorDetail(StrictModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2000)
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(StrictModel):
    error: ErrorDetail
    request_id: OpaqueId
    trace_id: OpaqueId


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class UserView(StrictModel):
    user_id: OpaqueId
    username: str = Field(min_length=1, max_length=128)
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserCreate(StrictModel):
    username: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)
    role: UserRole


class UserPatch(StrictModel):
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)
