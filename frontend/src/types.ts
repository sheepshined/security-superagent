export type Severity = "low" | "medium" | "high" | "critical";
export type Verdict =
  | "true_positive"
  | "false_positive"
  | "needs_review"
  | "duplicate";

export interface ScenarioSummary {
  scenario_id: string;
  title: string;
  description: string;
  severity: Severity;
  expected_verdict: Verdict;
  supports_superagent: boolean;
}

export interface RuntimeReadiness {
  status: "ready" | "not_ready";
  dependencies: {
    xdr: string;
    model: "deepseek" | "mock";
    langsmith: "enabled" | "disabled";
    scenario_count: number;
    sqlite: "ready" | "not_ready";
    worker: "ready" | "not_ready";
    auth: "ready" | "not_initialized";
    chroma: string;
    knowledge_index: string;
    knowledge_chunks: number;
    onnx: string;
    mcp: string;
  };
}

export interface JobAccepted {
  job_id: string;
  case_id: string;
  status: string;
  request_id: string;
  trace_id: string;
  analysis_version: number;
  parent_job_id: string | null;
  workflow_version_id: string;
}

export interface AuthUser {
  user_id: string;
  username: string;
  role: "admin" | "analyst" | "approver";
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface JobEvent {
  event_id: number;
  job_id: string;
  event_type: string;
  status: string;
  current_node: string | null;
  occurred_at: string;
  data: Record<string, unknown>;
}

export interface TraceReplay {
  trace_id: string;
  checkpoint_count: number;
  checkpoints: Array<{
    checkpoint_id: string;
    trace_id: string;
    created_at: string;
    step: number;
    source: string;
    last_node: string | null;
    next_nodes: string[];
    evidence_count: number;
    citation_count: number;
    react_tool_calls: number;
    verdict: Verdict | null;
  }>;
}

export interface Evidence {
  evidence_id: string;
  type: string;
  source: string;
  statement: string;
  observed_at: string;
  confidence: number;
}

export interface Citation {
  citation_id: string;
  document_id: string;
  chunk_id: string;
  title: string;
  source_uri: string;
  version: string;
  excerpt: string;
}

export interface InvestigationTrace {
  analyst_query: string;
  plan: {
    strategy: "plan_execute_verify_replan";
    objective: string;
    max_replans: number;
    max_react_iterations: number;
    max_tool_calls: number;
    steps: Array<{
      step_id: string;
      agent: "network_agent" | "endpoint_agent" | "identity_agent" | "knowledge_agent";
      objective: string;
      required_evidence_types: string[];
      read_only: boolean;
    }>;
  };
  plan_executions: Array<{
    step_id: string;
    agent: string;
    tool_name: string;
    status: "completed" | "failed" | "denied";
    evidence_ids: string[];
    citation_ids: string[];
    observation_summary: string;
  }>;
  critic: {
    evidence_sufficient: boolean;
    requires_react: boolean;
    missing_evidence_types: string[];
    conflicts: string[];
    rationale: string;
  };
  react_steps: Array<{
    iteration: number;
    decision_summary: string;
    tool_name: string;
    tool_input: Record<string, string>;
    observation_summary: string;
    new_evidence_ids: string[];
    status: "completed" | "denied" | "failed";
  }>;
  replans: number;
  tool_calls: number;
  replan_history: Array<{
    replan_id: string;
    reason: string;
    missing_evidence_types: string[];
    original_step_ids: string[];
    added_step_ids: string[];
    removed_step_ids: string[];
  }>;
  stop_reason:
    | "not_required"
    | "evidence_acquired"
    | "evidence_exhausted"
    | "iteration_limit"
    | "tool_call_limit"
    | "tool_denied"
    | "human_review_required";
}

export interface TriageResult {
  job_id: string;
  case_id: string;
  verdict: Verdict;
  risk_score: number;
  confidence: number;
  summary: string;
  evidence: Evidence[];
  citations: Citation[];
  unknowns: string[];
  recommended_actions: Array<{
    tool_name: string;
    target: string;
    risk_level: string;
    dry_run: boolean;
    requires_approval: boolean;
  }>;
  requires_superagent: boolean;
  workflow_trace: string[];
  model_assessment: Record<string, unknown> | null;
  investigation: InvestigationTrace | null;
  model_health: "HEALTHY" | "DEGRADED" | "NOT_CONFIGURED";
  degraded_reason: string | null;
  agent_reports: Array<{
    agent: "network_agent" | "endpoint_agent" | "identity_agent" | "knowledge_agent";
    objective: string;
    status: "completed" | "partial" | "failed";
    evidence_ids: string[];
    citation_ids: string[];
    observations: string[];
    unknowns: string[];
    tool_calls: number;
    started_at: string;
    completed_at: string;
  }>;
  conflicts: Array<{
    conflict_id: string;
    topic: string;
    evidence_ids: string[];
    statements: string[];
    status: "unresolved" | "resolved_as_authorized_activity" | "resolved_as_threat";
    resolution: string;
  }>;
}

export type ActionStatus =
  | "PROPOSED"
  | "DRY_RUN_PASSED"
  | "PENDING_APPROVAL"
  | "APPROVED"
  | "REJECTED"
  | "EXECUTING"
  | "SUCCEEDED"
  | "FAILED"
  | "VERIFIED"
  | "ROLLED_BACK"
  | "EXPIRED";

export interface ResponseAction {
  action_id: string;
  job_id: string;
  tool_name: string;
  target: string;
  scope: Record<string, unknown>;
  reason: string;
  expected_effect: string;
  rollback: string;
  risk_level: string;
  status: ActionStatus;
  plan_hash: string;
  requested_by: string;
  requested_by_username: string;
  approvals: Array<{
    approval_id: string;
    reviewer_id: string;
    reviewer_username: string;
    decision: "approved" | "rejected";
    comment: string;
    decided_at: string;
  }>;
  dry_run_result: Record<string, unknown> | null;
  execution_result: Record<string, unknown> | null;
  verification_result: Record<string, unknown> | null;
  expires_at: string;
  created_at: string;
  updated_at: string;
}

export interface ToolDescriptor {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  risk_level: string;
  allowed_roles: AuthUser["role"][];
  timeout_seconds: number;
  side_effects: boolean;
  requires_approval: boolean;
  idempotency_strategy: "read_only" | "explicit_key" | "plan_hash";
}

export interface KnowledgeSource {
  source_id: string;
  name: string;
  source_type: string;
  uri: string;
  version: string;
  license: string;
  access_labels: string[];
  enabled: boolean;
}

export interface KnowledgeMetrics {
  active_index_id: string | null;
  index_count: number;
  ready_index_count: number;
  source_count: number;
  enabled_source_count: number;
  document_count: number;
  chunk_count: number;
  chroma_enabled: boolean;
}

export interface WorkflowConfig {
  node_enabled: Record<string, boolean>;
  model: string;
  node_timeout_seconds: number;
  retries: number;
  max_tool_calls: number;
  max_replans: number;
  agents: string[];
  tool_allowlist: string[];
  knowledge_source_ids: string[];
}

export interface WorkflowTemplate {
  template_id: string;
  name: string;
  description: string;
  fixed_nodes: string[];
  configurable_nodes: string[];
  default_config: WorkflowConfig;
}

export interface WorkflowVersion {
  workflow_version_id: string;
  template_id: string;
  version_number: number;
  config: WorkflowConfig;
  config_hash: string;
  status: "draft" | "validated" | "active";
  created_by: string;
  created_at: string;
  activated_at: string | null;
}
