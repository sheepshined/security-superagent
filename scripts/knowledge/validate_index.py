import argparse
import json
from pathlib import Path
from time import perf_counter

from app.rag.catalog import KnowledgeCatalog
from app.rag.index import KnowledgeIndexManager


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the active knowledge index")
    parser.add_argument("--index-root", type=Path, required=True)
    parser.add_argument("--registry", type=Path, default=Path("knowledge/sources.json"))
    parser.add_argument("--model-cache", type=Path, required=True)
    args = parser.parse_args()

    catalog = KnowledgeCatalog(args.index_root, args.registry)
    started = perf_counter()
    retriever = KnowledgeIndexManager(
        catalog,
        model_cache=args.model_cache,
    ).create_retriever()
    report: dict[str, object] = {
        "metrics": catalog.metrics().model_dump(mode="json"),
        "load_seconds": round(perf_counter() - started, 3),
        "queries": [],
    }
    queries = [
        "T1021.002 SMB 横向移动 PsExec",
        "CVE 已知在野利用 修复期限",
        "个人信息泄露 外部模型 最小必要",
        "WinRM remote service lateral movement",
    ]
    query_reports: list[dict[str, object]] = []
    for query in queries:
        started = perf_counter()
        hits = retriever.search(
            query,
            top_k=3,
            allowed_access_labels={"public", "synthetic"},
            scenario="lateral_movement",
        )
        query_reports.append(
            {
                "query": query,
                "seconds": round(perf_counter() - started, 3),
                "hits": [
                    {
                        "document_id": hit.chunk.document_id,
                        "source_id": hit.chunk.metadata.get("source_id"),
                        "score": hit.final_score,
                        "semantic_score": hit.semantic_score,
                    }
                    for hit in hits
                ],
            }
        )
    report["queries"] = query_reports
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
