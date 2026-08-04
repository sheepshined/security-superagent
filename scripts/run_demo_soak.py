"""Run the synthetic SUP-001 main demo repeatedly against a live API."""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

TERMINAL = {"SUCCEEDED", "DEGRADED", "FAILED", "REJECTED", "EXPIRED", "CANCELLED"}


class DemoClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.csrf_token = ""

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        merged_headers = {"Accept": "application/json"}
        if body is not None:
            merged_headers["Content-Type"] = "application/json"
        if self.csrf_token and method != "GET":
            merged_headers["X-CSRF-Token"] = self.csrf_token
        merged_headers.update(headers or {})
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers=merged_headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=130) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} from {method} {path}") from exc

    def login_from_environment(self) -> None:
        username = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "")
        password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
        if not username or not password:
            return
        self.request(
            "/api/v1/auth/login",
            method="POST",
            payload={"username": username, "password": password},
        )
        for cookie in self.cookies:
            if cookie.name == "ssa_csrf":
                self.csrf_token = cookie.value
                return
        raise RuntimeError("login succeeded without a CSRF cookie")


def wait_for_result(client: DemoClient, job_id: str, timeout_seconds: int) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        job = client.request(f"/api/v1/jobs/{job_id}")
        status = str(job["status"])
        if status in TERMINAL:
            if status != "SUCCEEDED":
                raise RuntimeError(f"job {job_id} ended as {status}")
            return client.request(f"/api/v1/jobs/{job_id}/result")
        time.sleep(0.5)
    raise RuntimeError(f"job {job_id} exceeded {timeout_seconds}s")


def validate_live_result(result: dict[str, object], analyst_query: str) -> None:
    investigation = result.get("investigation") or {}
    checks = {
        "verdict": result.get("verdict") == "true_positive",
        "model_health": result.get("model_health") == "HEALTHY",
        "agent_reports": len(result.get("agent_reports") or []) >= 3,
        "citations": len(result.get("citations") or []) >= 1,
        "recommended_actions": len(result.get("recommended_actions") or []) >= 1,
        "analyst_query": (
            isinstance(investigation, dict)
            and investigation.get("analyst_query") == analyst_query
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError(f"live result failed checks: {', '.join(failed)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--analyst-query",
        default="Verify the cross-domain lateral movement chain and approved-change context.",
    )
    args = parser.parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be positive")

    client = DemoClient(args.base_url)
    readiness = client.request("/readyz")
    dependencies = readiness.get("dependencies") or {}
    if not isinstance(dependencies, dict) or dependencies.get("model") != "deepseek":
        raise SystemExit("refusing soak: readiness does not report model=deepseek")
    client.login_from_environment()

    durations: list[float] = []
    for run_number in range(1, args.runs + 1):
        started = time.monotonic()
        accepted = client.request(
            "/api/v1/triage/jobs",
            method="POST",
            headers={"Idempotency-Key": f"live-soak-{uuid.uuid4().hex}"},
            payload={
                "alert_ref": {"source": "mock-xdr", "external_id": "A-SUP-001"},
                "analyst_query": args.analyst_query,
                "mode": "mock",
                "options": {
                    "time_window_minutes": 60,
                    "allow_superagent_escalation": True,
                    "max_risk_level": "high",
                },
            },
        )
        result = wait_for_result(client, str(accepted["job_id"]), args.timeout)
        validate_live_result(result, args.analyst_query)
        duration = time.monotonic() - started
        durations.append(duration)
        print(json.dumps({"run": run_number, "status": "passed", "seconds": round(duration, 2)}))

    print(
        json.dumps(
            {
                "runs": len(durations),
                "passed": len(durations),
                "average_seconds": round(sum(durations) / len(durations), 2),
                "max_seconds": round(max(durations), 2),
                "provider": "deepseek",
                "data_classification": "synthetic",
            }
        )
    )


if __name__ == "__main__":
    main()
