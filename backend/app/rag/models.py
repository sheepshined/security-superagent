from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.domain.models import StrictModel


class KnowledgeDocument(StrictModel):
    document_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    source_uri: str = Field(min_length=1, max_length=2048)
    version: str = Field(min_length=1, max_length=128)
    published_at: datetime
    effective_at: datetime
    source_quality: float = Field(ge=0, le=1)
    license: str = Field(default="unknown", min_length=1, max_length=256)
    fetched_at: datetime | None = None
    content_hash: str | None = Field(default=None, min_length=16, max_length=128)
    parent_document_id: str | None = Field(default=None, max_length=128)
    source_type: str = Field(default="reference", min_length=1, max_length=64)
    deleted: bool = False
    access_labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    content: str = Field(min_length=1, max_length=20000)


class KnowledgeChunk(StrictModel):
    chunk_id: str = Field(min_length=1, max_length=160)
    document_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    source_uri: str = Field(min_length=1, max_length=2048)
    version: str = Field(min_length=1, max_length=128)
    source_quality: float = Field(ge=0, le=1)
    license: str = Field(default="unknown", min_length=1, max_length=256)
    fetched_at: datetime | None = None
    content_hash: str = Field(min_length=16, max_length=128)
    parent_document_id: str = Field(min_length=1, max_length=128)
    source_type: str = Field(default="reference", min_length=1, max_length=64)
    access_labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    content: str = Field(min_length=1, max_length=4000)


class SearchHit(StrictModel):
    chunk: KnowledgeChunk
    keyword_score: float = Field(ge=0, le=1)
    semantic_score: float = Field(ge=0, le=1)
    final_score: float = Field(ge=0, le=1)


class KnowledgeSource(StrictModel):
    source_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    source_type: Literal["stix", "json", "csv", "yaml", "html", "pdf", "snapshot"]
    uri: str = Field(min_length=1, max_length=2048)
    version: str = Field(min_length=1, max_length=128)
    license: str = Field(min_length=1, max_length=256)
    license_uri: str = Field(min_length=1, max_length=2048)
    access_labels: list[str] = Field(default_factory=lambda: ["public"])
    enabled: bool = True
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    parser_options: dict[str, Any] = Field(default_factory=dict)


class SourceFetchRecord(StrictModel):
    source_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    uri: str = Field(min_length=1, max_length=2048)
    license: str = Field(min_length=1, max_length=256)
    fetched_at: datetime
    content_hash: str = Field(min_length=64, max_length=64)
    byte_count: int = Field(ge=0)
    cache_path: str = Field(min_length=1, max_length=2048)


class KnowledgeIndexManifest(StrictModel):
    index_id: str = Field(min_length=1, max_length=128)
    status: Literal["building", "ready", "failed"]
    created_at: datetime
    completed_at: datetime | None = None
    embedding_model: str = Field(min_length=1, max_length=256)
    chunk_count: int = Field(ge=0)
    document_count: int = Field(ge=0)
    source_ids: list[str] = Field(default_factory=list)
    deleted_document_ids: list[str] = Field(default_factory=list)
    content_hash: str = Field(min_length=16, max_length=128)
    chroma_enabled: bool = False
    error: str | None = Field(default=None, max_length=2000)


class KnowledgeMetrics(StrictModel):
    active_index_id: str | None = None
    index_count: int = Field(ge=0)
    ready_index_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    enabled_source_count: int = Field(ge=0)
    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    chroma_enabled: bool = False
