from datetime import datetime
from typing import Any

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
    access_labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    content: str = Field(min_length=1, max_length=4000)


class SearchHit(StrictModel):
    chunk: KnowledgeChunk
    keyword_score: float = Field(ge=0, le=1)
    semantic_score: float = Field(ge=0, le=1)
    final_score: float = Field(ge=0, le=1)
