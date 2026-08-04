import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.rag.catalog import KnowledgeCatalog, KnowledgeCatalogError
from app.rag.hybrid import HybridRetriever
from app.rag.models import KnowledgeChunk, KnowledgeDocument, KnowledgeIndexManifest

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CHUNK_SIZE = 1400
CHUNK_OVERLAP = 180


def _chunk_text(content: str) -> list[str]:
    normalized = re.sub(r"\n{3,}", "\n\n", content.strip())
    if len(normalized) <= CHUNK_SIZE:
        return [normalized]
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        hard_end = min(start + CHUNK_SIZE, len(normalized))
        end = hard_end
        if hard_end < len(normalized):
            candidates = [
                normalized.rfind(separator, start + CHUNK_SIZE // 2, hard_end)
                for separator in ("\n\n", "。", "；", ". ")
            ]
            end = max(candidates)
            if end <= start:
                end = hard_end
            else:
                end += 1
        value = normalized[start:end].strip()
        if value:
            chunks.append(value)
        if end >= len(normalized):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)
    return chunks


def documents_to_chunks(documents: list[KnowledgeDocument]) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    seen_hashes: set[str] = set()
    for document in sorted(documents, key=lambda item: item.document_id):
        if document.deleted:
            continue
        for index, value in enumerate(_chunk_text(document.content), start=1):
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            chunks.append(
                KnowledgeChunk(
                    chunk_id=f"{document.document_id}:{index:04d}",
                    document_id=document.document_id,
                    title=document.title,
                    source_uri=document.source_uri,
                    version=document.version,
                    source_quality=document.source_quality,
                    license=document.license,
                    fetched_at=document.fetched_at,
                    content_hash=digest,
                    parent_document_id=document.parent_document_id or document.document_id,
                    source_type=document.source_type,
                    access_labels=document.access_labels,
                    metadata=document.metadata,
                    content=value,
                )
            )
    return chunks


class FastEmbedChromaProvider:
    def __init__(self, generation_path: Path, model_name: str, cache_dir: Path) -> None:
        self.generation_path = generation_path
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model: Any | None = None
        self._collection: Any | None = None

    def _load(self) -> tuple[Any, Any]:
        if self._model is None or self._collection is None:
            import chromadb
            from fastembed import TextEmbedding

            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._model = TextEmbedding(
                model_name=self.model_name,
                cache_dir=str(self.cache_dir),
            )
            client = chromadb.PersistentClient(path=str(self.generation_path / "chroma"))
            self._collection = client.get_collection("security_knowledge")
        return self._model, self._collection

    def score(self, query: str, chunks: list[KnowledgeChunk]) -> dict[str, float]:
        if not chunks:
            return {}
        allowed_ids = {item.chunk_id for item in chunks}
        try:
            model, collection = self._load()
            embedding = next(iter(model.embed([query]))).tolist()
            collection_count = collection.count()
            result = collection.query(
                query_embeddings=[embedding],
                n_results=min(200, collection_count),
                include=["distances"],
            )
            scores = {chunk_id: 0.0 for chunk_id in allowed_ids}
            for chunk_id, distance in zip(
                result.get("ids", [[]])[0],
                result.get("distances", [[]])[0],
                strict=False,
            ):
                if chunk_id in allowed_ids:
                    scores[chunk_id] = max(0.0, min(1.0, 1.0 - float(distance)))
            return scores
        except (ImportError, OSError, RuntimeError, ValueError):
            return {chunk_id: 0.0 for chunk_id in allowed_ids}


class KnowledgeIndexManager:
    def __init__(
        self,
        catalog: KnowledgeCatalog,
        *,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        model_cache: Path | None = None,
    ) -> None:
        self.catalog = catalog
        self.embedding_model = embedding_model
        self.model_cache = (model_cache or catalog.index_root / "models").resolve()

    def build(
        self,
        documents: list[KnowledgeDocument],
        *,
        index_id: str | None = None,
        enable_chroma: bool = True,
        activate: bool = True,
    ) -> KnowledgeIndexManifest:
        timestamp = datetime.now(UTC)
        index_id = index_id or f"idx-{timestamp:%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", index_id):
            raise KnowledgeCatalogError("invalid index id")
        final_path = self.catalog.generations_dir / index_id
        if final_path.exists():
            raise KnowledgeCatalogError("index id already exists")
        staging = self.catalog.generations_dir / f".{index_id}.building-{uuid4().hex[:8]}"
        staging.mkdir(parents=True)
        chunks = documents_to_chunks(documents)
        previous_ids = {item.document_id for item in self.load_active_chunks()}
        current_ids = {item.document_id for item in chunks}
        deleted_ids = sorted(previous_ids - current_ids)
        source_ids = sorted(
            {
                str(item.metadata.get("source_id"))
                for item in chunks
                if item.metadata.get("source_id")
            }
        )
        aggregate_hash = hashlib.sha256(
            "".join(sorted(item.content_hash for item in chunks)).encode("ascii")
        ).hexdigest()
        building = KnowledgeIndexManifest(
            index_id=index_id,
            status="building",
            created_at=timestamp,
            embedding_model=self.embedding_model,
            chunk_count=len(chunks),
            document_count=len(current_ids),
            source_ids=source_ids,
            deleted_document_ids=deleted_ids,
            content_hash=aggregate_hash,
            chroma_enabled=False,
        )
        self._write_manifest(staging, building)
        try:
            self._write_chunks(staging, chunks)
            chroma_enabled = self._build_chroma(staging, chunks) if enable_chroma else False
            ready = building.model_copy(
                update={
                    "status": "ready",
                    "completed_at": datetime.now(UTC),
                    "chroma_enabled": chroma_enabled,
                }
            )
            self._write_manifest(staging, ready)
            os.replace(staging, final_path)
            if activate:
                self.catalog.activate(index_id)
            return ready
        except Exception as exc:
            failed = building.model_copy(
                update={
                    "status": "failed",
                    "completed_at": datetime.now(UTC),
                    "error": str(exc)[:2000],
                }
            )
            self._write_manifest(staging, failed)
            os.replace(staging, final_path)
            raise

    @staticmethod
    def _write_manifest(path: Path, manifest: KnowledgeIndexManifest) -> None:
        (path / "manifest.json").write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _write_chunks(path: Path, chunks: list[KnowledgeChunk]) -> None:
        with (path / "chunks.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for chunk in chunks:
                handle.write(chunk.model_dump_json())
                handle.write("\n")

    def _build_chroma(self, path: Path, chunks: list[KnowledgeChunk]) -> bool:
        if not chunks:
            return False
        try:
            import chromadb
            from fastembed import TextEmbedding
        except ImportError:
            return False
        self.model_cache.mkdir(parents=True, exist_ok=True)
        model = TextEmbedding(model_name=self.embedding_model, cache_dir=str(self.model_cache))
        with chromadb.PersistentClient(path=str(path / "chroma")) as client:
            collection = client.create_collection(
                "security_knowledge",
                metadata={"hnsw:space": "cosine", "embedding_model": self.embedding_model},
            )
            batch_size = 128
            for start in range(0, len(chunks), batch_size):
                batch = chunks[start : start + batch_size]
                embeddings = [
                    item.tolist() for item in model.embed([item.content for item in batch])
                ]
                collection.add(
                    ids=[item.chunk_id for item in batch],
                    documents=[item.content for item in batch],
                    embeddings=embeddings,
                    metadatas=[
                        {
                            "document_id": item.document_id,
                            "source_uri": item.source_uri,
                            "version": item.version,
                            "content_hash": item.content_hash,
                            "access_labels": json.dumps(item.access_labels, ensure_ascii=False),
                        }
                        for item in batch
                    ],
                )
        return True

    def load_active_chunks(self) -> list[KnowledgeChunk]:
        active_path = self.catalog.active_index_path
        return self._load_chunks(active_path) if active_path else []

    @staticmethod
    def _load_chunks(path: Path | None) -> list[KnowledgeChunk]:
        if not path or not (path / "chunks.jsonl").is_file():
            return []
        chunks: list[KnowledgeChunk] = []
        with (path / "chunks.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    chunks.append(KnowledgeChunk.model_validate_json(line))
        return chunks

    def create_retriever(self) -> HybridRetriever:
        active_path = self.catalog.active_index_path
        chunks = self._load_chunks(active_path)
        if not active_path or not chunks:
            return HybridRetriever()
        manifest = KnowledgeIndexManifest.model_validate_json(
            (active_path / "manifest.json").read_text(encoding="utf-8")
        )
        provider = (
            FastEmbedChromaProvider(active_path, manifest.embedding_model, self.model_cache)
            if manifest.chroma_enabled
            else None
        )
        return HybridRetriever(chunks=chunks, semantic_provider=provider)
