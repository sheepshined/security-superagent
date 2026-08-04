from scripts.check_critical_coverage import CRITICAL_FILES, check_critical_coverage


def _report(*, missing: bool = False) -> dict:
    return {
        "files": {
            path: {
                "missing_lines": [10] if missing and index == 0 else [],
                "missing_branches": [[10, 11]] if missing and index == 0 else [],
            }
            for index, path in enumerate(CRITICAL_FILES)
        }
    }


def test_critical_coverage_gate_accepts_complete_security_boundary() -> None:
    assert check_critical_coverage(_report()) == []


def test_critical_coverage_gate_reports_missing_paths_lines_and_branches() -> None:
    report = _report(missing=True)
    report["files"].pop(CRITICAL_FILES[-1])

    failures = check_critical_coverage(report)

    assert "missing_lines=[10]" in failures[0]
    assert "missing_branches=[[10, 11]]" in failures[0]
    assert failures[-1].endswith("missing from coverage report")
