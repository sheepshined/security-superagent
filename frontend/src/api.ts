import type {
  AuthUser,
  JobAccepted,
  JobEvent,
  RuntimeReadiness,
  ScenarioSummary,
  TraceReplay,
  TriageResult
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number
  ) {
    super(message);
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const csrfToken = document.cookie
    .split("; ")
    .find((item) => item.startsWith("ssa_csrf="))
    ?.split("=", 2)[1];
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(method !== "GET" && method !== "HEAD" && csrfToken
        ? {"X-CSRF-Token": decodeURIComponent(csrfToken)}
        : {}),
      ...init?.headers
    },
    ...init
  });
  if (!response.ok) {
    throw new ApiError(`请求失败：${response.status}`, response.status);
  }
  return (await response.json()) as T;
}

export function login(username: string, password: string): Promise<AuthUser> {
  return request<AuthUser>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({username, password})
  });
}

export function getMe(): Promise<AuthUser> {
  return request<AuthUser>("/api/v1/auth/me");
}

export async function logout(): Promise<void> {
  const csrfToken = document.cookie
    .split("; ")
    .find((item) => item.startsWith("ssa_csrf="))
    ?.split("=", 2)[1];
  const response = await fetch("/api/v1/auth/logout", {
    method: "POST",
    credentials: "same-origin",
    headers: csrfToken ? {"X-CSRF-Token": decodeURIComponent(csrfToken)} : {}
  });
  if (!response.ok) {
    throw new ApiError(`请求失败：${response.status}`, response.status);
  }
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
    headers: {"Idempotency-Key": crypto.randomUUID()},
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

export function streamJob(
  jobId: string,
  onEvent: (event: JobEvent) => void
): Promise<void> {
  return new Promise((resolve, reject) => {
    const source = new EventSource(`/api/v1/jobs/${jobId}/stream`, {
      withCredentials: true
    });
    const eventNames = [
      "job_created",
      "job_status_changed",
      "node_completed",
      "plan_created",
      "agent_step_completed",
      "agent_completed",
      "tool_completed",
      "model_completed",
      "model_skipped",
      "model_degraded",
      "critic_completed",
      "replan_created",
      "replan_completed",
      "policy_finalized",
      "result_saved",
      "job_error"
    ];
    let terminal = false;
    const receive = (raw: Event) => {
      const message = raw as MessageEvent<string>;
      const event = JSON.parse(message.data) as JobEvent;
      onEvent(event);
      if (["SUCCEEDED", "DEGRADED", "FAILED"].includes(event.status)) {
        terminal = true;
        source.close();
        resolve();
      }
    };
    eventNames.forEach((name) => source.addEventListener(name, receive));
    source.onerror = () => {
      source.close();
      if (terminal) {
        resolve();
      } else {
        reject(new Error("SSE 任务流连接中断"));
      }
    };
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
