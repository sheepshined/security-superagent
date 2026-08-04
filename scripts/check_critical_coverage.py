"""Fail CI unless every branch in the security-critical boundary is tested."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

CRITICAL_FILES = (
    "backend/app/services/auth.py",
    "backend/app/services/actions.py",
    "backend/app/services/triage.py",
    "backend/app/tools/registry.py",
)


def _normalized(value: str) -> str:
    return value.replace("\\", "/")


def check_critical_coverage(report: dict[str, Any]) -> list[str]:
    files = {_normalized(name): details for name, details in report.get("files", {}).items()}
    failures: list[str] = []
    for expected in CRITICAL_FILES:
        details = files.get(expected)
        if details is None:
            failures.append(f"{expected}: missing from coverage report")
            continue
        missing_lines = details.get("missing_lines", [])
        missing_branches = details.get("missing_branches", [])
        if missing_lines or missing_branches:
            failures.append(
                f"{expected}: missing_lines={missing_lines}, "
                f"missing_branches={missing_branches}"
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if report.get("meta", {}).get("branch_coverage") is not True:
        print("critical coverage gate failed: report does not include branch coverage")
        return 1
    failures = check_critical_coverage(report)
    if failures:
        print("critical coverage gate failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("critical security coverage: 100% lines and branches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
