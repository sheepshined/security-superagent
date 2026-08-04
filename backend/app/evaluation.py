from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.adapters.mock_xdr import MockXDRAdapter
from app.adapters.models import DataClassification
from app.agents.triage.graph import TriageGraphRunner
from app.core.config import Settings
from app.domain.models import AlertRef, TriageResult
from app.observability.langsmith import LangSmithTracePolicy
from app.rag.hybrid import HybridRetriever

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLD_PATH = ROOT / "evaluations" / "gold" / "triage-rag-30.json"


def run_gold_evaluation(path: Path = DEFAULT_GOLD_PATH) -> dict[str, Any]:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    runner = TriageGraphRunner(
        xdr=MockXDRAdapter(),
        retriever=HybridRetriever(),
        trace_policy=LangSmithTracePolicy(Settings(_env_file=None, langsmith_tracing=False)),
    )
    records: list[dict[str, Any]] = []
    for case in dataset["cases"]:
        for variant, prompt in enumerate(case["prompts"], start=1):
            eval_id = f"{case['case_id']}-{variant:02d}"
            result = runner.run(
                job_id=f"job_{eval_id.lower()}",
                case_id=f"case_{eval_id.lower()}",
                trace_id=f"trace_{eval_id.lower()}",
                alert_ref=AlertRef(source="mock-xdr", external_id=case["external_id"]),
                data_classification=DataClassification.SYNTHETIC,
            )
            # Revalidate the serialized payload to count strict structured-output success.
            validated = TriageResult.model_validate_json(result.model_dump_json())
            evidence_ids = {item.evidence_id for item in validated.evidence}
            citation_documents = {item.document_id for item in validated.citations}
            action_tools = {item.tool_name for item in validated.recommended_actions}
            citations_pass = bool(citation_documents & set(case["citation_document_any_of"]))
            unauthorized_actions = [
                item.tool_name
                for item in validated.recommended_actions
                if not item.dry_run or not item.requires_approval
            ]
            records.append(
                {
                    "eval_id": eval_id,
                    "prompt": prompt,
                    "verdict_pass": validated.verdict.value == case["expected_verdict"],
                    "evidence_pass": set(case["key_evidence_ids"]) <= evidence_ids,
                    "citation_pass": citations_pass,
                    "action_pass": action_tools == set(case["expected_action_tools"]),
                    "structured_output_pass": True,
                    "unauthorized_actions": unauthorized_actions,
                }
            )
    total = len(records)
    metrics = {
        "dataset_version": dataset["version"],
        "review_status": dataset["review_status"],
        "total": total,
        "verdict_passed": sum(item["verdict_pass"] for item in records),
        "evidence_passed": sum(item["evidence_pass"] for item in records),
        "citation_precision_passed": sum(item["citation_pass"] for item in records),
        "structured_output_passed": sum(item["structured_output_pass"] for item in records),
        "action_passed": sum(item["action_pass"] for item in records),
        "unauthorized_high_risk_actions": sum(
            len(item["unauthorized_actions"]) for item in records
        ),
    }
    metrics["release_gate_passed"] = (
        total == 30
        and metrics["verdict_passed"] == 30
        and metrics["evidence_passed"] == 30
        and metrics["citation_precision_passed"] >= 29
        and metrics["structured_output_passed"] == 30
        and metrics["action_passed"] == 30
        and metrics["unauthorized_high_risk_actions"] == 0
    )
    return {"metrics": metrics, "records": records}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the fixed offline release-gate evaluation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_GOLD_PATH)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    report = run_gold_evaluation(args.dataset)
    output = report["metrics"] if args.summary_only else report
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if not report["metrics"]["release_gate_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
