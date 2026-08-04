import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.rag.catalog import KnowledgeCatalog
from app.rag.index import KnowledgeIndexManager
from app.rag.models import KnowledgeDocument, SourceFetchRecord
from app.rag.pipeline import KnowledgeDownloader, KnowledgeParser

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _paths() -> tuple[KnowledgeCatalog, KnowledgeDownloader, KnowledgeIndexManager]:
    settings = get_settings()
    catalog = KnowledgeCatalog(
        Path(settings.chroma_persist_directory),
        Path(settings.knowledge_source_registry),
    )
    downloader = KnowledgeDownloader(Path(settings.knowledge_cache_directory))
    manager = KnowledgeIndexManager(
        catalog,
        embedding_model=settings.knowledge_embedding_model,
        model_cache=Path(settings.fastembed_cache_path),
    )
    return catalog, downloader, manager


def _latest_fetch_record(cache_root: Path, source_id: str) -> SourceFetchRecord | None:
    records: list[SourceFetchRecord] = []
    for path in cache_root.glob(f"{source_id}-*.manifest.json"):
        try:
            records.append(SourceFetchRecord.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return max(records, key=lambda item: item.fetched_at) if records else None


def fetch_sources(*, allow_partial: bool) -> dict[str, str]:
    catalog, downloader, _ = _paths()
    outcomes: dict[str, str] = {}
    for source in catalog.list_sources():
        if not source.enabled or source.source_type == "snapshot":
            continue
        try:
            record = downloader.fetch(source)
            outcomes[source.source_id] = record.content_hash
        except Exception as exc:
            outcomes[source.source_id] = f"ERROR: {str(exc)[:300]}"
            if not allow_partial:
                raise
    return outcomes


def _snapshot_documents(exclude_source_ids: set[str]) -> list[KnowledgeDocument]:
    documents: list[KnowledgeDocument] = []
    for path in sorted((PROJECT_ROOT / "knowledge" / "snapshots").glob("*.json")):
        for raw in json.loads(path.read_text(encoding="utf-8")):
            document = KnowledgeDocument.model_validate(raw)
            source_id = str(document.metadata.get("source_id", ""))
            if source_id not in exclude_source_ids:
                documents.append(document)
    return documents


def parse_cached_sources(*, allow_partial: bool) -> list[KnowledgeDocument]:
    catalog, downloader, _ = _paths()
    parser = KnowledgeParser()
    sources = catalog.list_sources()
    records = {
        source.source_id: _latest_fetch_record(downloader.cache_root, source.source_id)
        for source in sources
        if source.source_type != "snapshot"
    }
    documents = _snapshot_documents(
        {source_id for source_id, record in records.items() if record is not None}
    )
    for source in sources:
        if not source.enabled or source.source_type == "snapshot":
            continue
        record = records.get(source.source_id)
        if not record:
            if allow_partial:
                continue
            raise RuntimeError(f"source has not been downloaded: {source.source_id}")
        path = Path(record.cache_path)
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != record.content_hash:
            raise RuntimeError(f"cached source hash mismatch: {source.source_id}")
        try:
            documents.extend(parser.parse(source, path, fetched_at=record.fetched_at))
        except Exception:
            if not allow_partial:
                raise
    return documents


def build_index(*, allow_partial: bool, activate: bool, no_chroma: bool) -> str:
    _, _, manager = _paths()
    documents = parse_cached_sources(allow_partial=allow_partial)
    manifest = manager.build(
        documents,
        enable_chroma=not no_chroma,
        activate=activate,
    )
    return manifest.index_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Versioned Security SuperAgent knowledge indexer")
    parser.add_argument("command", choices=["fetch", "build", "sync"])
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--no-chroma", action="store_true")
    args = parser.parse_args()

    result: dict[str, object] = {
        "command": args.command,
        "started_at": datetime.now(UTC).isoformat(),
    }
    if args.command in {"fetch", "sync"}:
        result["sources"] = fetch_sources(allow_partial=args.allow_partial)
    if args.command in {"build", "sync"}:
        result["index_id"] = build_index(
            allow_partial=args.allow_partial,
            activate=args.activate,
            no_chroma=args.no_chroma,
        )
    result["completed_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
