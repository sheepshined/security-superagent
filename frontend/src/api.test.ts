import {beforeEach, describe, expect, it, vi} from "vitest";

import {ApiError, listScenarios, submitTriage} from "./api";

describe("API client security headers", () => {
  beforeEach(() => {
    document.cookie = "ssa_csrf=test-csrf-token; path=/";
    vi.stubGlobal("crypto", {randomUUID: () => "fixed-idempotency-key"});
  });

  it("merges CSRF, content type, and idempotency headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
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
    vi.stubGlobal("fetch", fetchMock);

    await submitTriage("A-SUP-001", "核验跨域横向移动攻击链");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.headers).toMatchObject({
      "Content-Type": "application/json",
      "X-CSRF-Token": "test-csrf-token",
      "Idempotency-Key": "fixed-idempotency-key"
    });
    expect(init.credentials).toBe("same-origin");
    expect(JSON.parse(String(init.body))).toMatchObject({
      analyst_query: "核验跨域横向移动攻击链"
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
