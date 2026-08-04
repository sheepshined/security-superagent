import type {
  JobAccepted,
  RuntimeReadiness,
  ScenarioSummary,
  TraceReplay,
  TriageResult
} from "./types";

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number
  ) {
    super(message);
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: {"Content-Type": "application/json", ...init?.headers},
    ...init
  });
  if (!response.ok) {
    throw new ApiError(`请求失败：${response.status}`, response.status);
  }
  return (await response.json()) as T;
}

export function listScenarios(): Promise<ScenarioSummary[]> {
  return request<ScenarioSummary[]>("/api/v1/scenarios");
}

export function getReadiness(): Promise<RuntimeReadiness> {
  return request<RuntimeReadiness>("/readyz");
}

export function submitTriage(externalId: string): Promise<JobAccepted> {
  return request<JobAccepted>("/api/v1/triage/jobs", {
    method: "POST",
    body: JSON.stringify({
      alert_ref: {source: "mock-xdr", external_id: externalId},
      mode: "mock",
      options: {
        time_window_minutes: 60,
        allow_superagent_escalation: true,
        max_risk_level: "high"
      }
    })
  });
}

export function getTriageResult(jobId: string): Promise<TriageResult> {
  return request<TriageResult>(`/api/v1/jobs/${jobId}/result`);
}

export function getTraceReplay(traceId: string): Promise<TraceReplay> {
  return request<TraceReplay>(`/api/v1/traces/${traceId}`);
}

export async function waitForTriageResult(
  jobId: string,
  attempts = 30,
  intervalMs = 200
): Promise<TriageResult> {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await getTriageResult(jobId);
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 409) {
        throw error;
      }
    }
    await new Promise((resolve) => window.setTimeout(resolve, intervalMs));
  }
  throw new Error("研判任务等待超时");
}
