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
}
