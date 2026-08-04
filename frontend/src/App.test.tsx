import {render, screen} from "@testing-library/react";
import {describe, expect, it, vi} from "vitest";

import App from "./App";

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    getMe: vi.fn().mockResolvedValue({
      user_id: "usr_test",
      username: "test-admin",
      role: "admin",
      is_active: true,
      created_at: "2026-08-04T00:00:00Z",
      updated_at: "2026-08-04T00:00:00Z"
    }),
    listScenarios: vi.fn().mockResolvedValue([
      {
        scenario_id: "SUP-001",
        title: "Cross-domain lateral movement attack chain",
        description: "Synthetic SuperAgent scenario",
        severity: "critical",
        expected_verdict: "true_positive",
        supports_superagent: true
      }
    ]),
    getReadiness: vi.fn().mockResolvedValue({
      status: "ready",
      dependencies: {
        xdr: "mock",
        model: "mock",
        langsmith: "disabled",
        scenario_count: 5,
        sqlite: "ready",
        worker: "ready",
        auth: "ready",
        chroma: "snapshot_fallback",
        knowledge_index: "snapshot_fallback",
        knowledge_chunks: 9,
        onnx: "not_cached",
        mcp: "configured"
      }
    })
  };
});

describe("Security SuperAgent console", () => {
  it("renders an authenticated SuperAgent operations overview", async () => {
    render(<App />);

    expect(await screen.findByText("Cross-domain lateral movement attack chain")).toBeVisible();
    expect(screen.getByText("Security SuperAgent")).toBeVisible();
    expect(screen.getByText("SuperAgent")).toBeVisible();
    expect(screen.getByText("预置安全场景")).toBeVisible();
  });
});
