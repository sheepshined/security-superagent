import json
import os
import re
from pathlib import Path

from app.rag.models import (
    KnowledgeIndexManifest,
    KnowledgeMetrics,
    KnowledgeSource,
)

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "knowledge" / "sources.json"
SAFE_INDEX_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class KnowledgeCatalogError(RuntimeError):
    pass


class KnowledgeCatalog:
    """Read-only source registry plus atomically switched index generations."""

    def __init__(self, index_root: Path, registry_path: Path = DEFAULT_REGISTRY_PATH) -> None:
        self.index_root = index_root.resolve()
        self.registry_path = registry_path.resolve()
        self.generations_dir = self.index_root / "generations"
        self.active_pointer = self.index_root / "ACTIVE"
        self.generations_dir.mkdir(parents=True, exist_ok=True)

    def list_sources(self) -> list[KnowledgeSource]:
        raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
        return [KnowledgeSource.model_validate(item) for item in raw]

    def get_source(self, source_id: str) -> KnowledgeSource:
        for source in self.list_sources():
            if source.source_id == source_id:
                return source
        raise KnowledgeCatalogError(f"unknown knowledge source: {source_id}")

    def list_indexes(self) -> list[KnowledgeIndexManifest]:
        manifests: list[KnowledgeIndexManifest] = []
        for path in sorted(self.generations_dir.glob("*/manifest.json"), reverse=True):
            try:
                manifests.append(
                    KnowledgeIndexManifest.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError):
                continue
        return sorted(manifests, key=lambda item: item.created_at, reverse=True)

    @property
    def active_index_id(self) -> str | None:
        try:
            value = self.active_pointer.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        return value if SAFE_INDEX_ID.fullmatch(value) else None

    @property
    def active_index_path(self) -> Path | None:
        index_id = self.active_index_id
        if not index_id:
            return None
        path = (self.generations_dir / index_id).resolve()
        if path.parent != self.generations_dir or not path.is_dir():
            return None
        return path

    def activate(self, index_id: str) -> KnowledgeIndexManifest:
        if not SAFE_INDEX_ID.fullmatch(index_id):
            raise KnowledgeCatalogError("invalid index id")
        generation = (self.generations_dir / index_id).resolve()
        if generation.parent != self.generations_dir:
            raise KnowledgeCatalogError("index path escaped the configured root")
        manifest_path = generation / "manifest.json"
        if not manifest_path.is_file():
            raise KnowledgeCatalogError("index manifest not found")
        manifest = KnowledgeIndexManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        if manifest.status != "ready":
            raise KnowledgeCatalogError("only a ready index can be activated")
        self.index_root.mkdir(parents=True, exist_ok=True)
        temporary = self.index_root / f".ACTIVE.{os.getpid()}.tmp"
        temporary.write_text(f"{index_id}\n", encoding="utf-8")
        os.replace(temporary, self.active_pointer)
        return manifest

    def metrics(self) -> KnowledgeMetrics:
        sources = self.list_sources()
        indexes = self.list_indexes()
        active_id = self.active_index_id
        active = next((item for item in indexes if item.index_id == active_id), None)
        return KnowledgeMetrics(
            active_index_id=active_id,
            index_count=len(indexes),
            ready_index_count=sum(item.status == "ready" for item in indexes),
            source_count=len(sources),
            enabled_source_count=sum(item.enabled for item in sources),
            document_count=active.document_count if active else 0,
            chunk_count=active.chunk_count if active else 0,
            chroma_enabled=active.chroma_enabled if active else False,
        )
