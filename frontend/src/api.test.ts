import {beforeEach, describe, expect, it, vi} from "vitest";

import {ApiError, listScenarios, submitTriage} from "./api";

describe("API client security headers", () => {
  beforeEach(() => {
    document.cookie = "ssa_csrf=test-csrf-token; path=/";
    vi.stubGlobal("crypto", {randomUUID: () => "fixed-idempotency-key"});
  });

  it("merges CSRF, content type, and idempotency headers", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === "/readyz") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              status: "ready",
              dependencies: {
                xdr: "mock",
                model: "mock",
                langsmith: "disabled",
                scenario_count: 5,
                sqlite: "ready",
                worker: "ready",
                auth: "ready",
                chroma: "ready",
                knowledge_index: "idx",
                knowledge_chunks: 0,
                onnx: "ready",
                mcp: "configured"
              }
            }),
            {status: 200, headers: {"Content-Type": "application/json"}}
          )
        );
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            job_id: "job_1",
            case_id: "case_1",
            status: "QUEUED",
            request_id: "req_1",
            trace_id: "tr_1",
            analysis_version: 1,
            parent_job_id: null,
            workflow_version_id: "workflow_default_v1"
          }),
          {status: 202, headers: {"Content-Type": "application/json"}}
        )
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    await submitTriage("A-SUP-001", "核验跨域横向移动攻击链");

    const calls = fetchMock.mock.calls as [string, RequestInit][];
    const submitCall = calls.find(([url]) => url === "/api/v1/triage/jobs");
    expect(submitCall).toBeDefined();
    const [, init] = submitCall!;
    expect(init.headers).toMatchObject({
      "Content-Type": "application/json",
      "X-CSRF-Token": "test-csrf-token",
      "Idempotency-Key": "fixed-idempotency-key"
    });
    expect(init.credentials).toBe("same-origin");
    expect(JSON.parse(String(init.body))).toMatchObject({
      analyst_query: "核验跨域横向移动攻击链",
      mode: "mock",
      alert_ref: {source: "mock-xdr", external_id: "A-SUP-001"}
    });
  });

  it("raises a typed error for an unsuccessful response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("denied", {status: 403})));

    try {
      await listScenarios();
      throw new Error("request unexpectedly succeeded");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(403);
    }
  });
});
