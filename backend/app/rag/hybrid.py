import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from app.rag.models import KnowledgeChunk, KnowledgeDocument, SearchHit

DEFAULT_SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "knowledge" / "snapshots"
TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_.-]+|[\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in TOKEN_PATTERN.findall(text.lower()):
        if raw.isascii():
            tokens.append(raw)
            tokens.extend(part for part in re.split(r"[._/-]", raw) if part and part != raw)
            continue
        tokens.extend(raw)
        tokens.extend(raw[index : index + 2] for index in range(max(0, len(raw) - 1)))
    return tokens


def hashing_vector(tokens: list[str], dimensions: int = 384) -> list[float]:
    vector = [0.0] * dimensions
    for token, count in Counter(tokens).items():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest, "big") % dimensions
        sign = 1.0 if digest[0] & 1 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def cosine_similarity(left: list[float], right: list[float]) -> float:
    similarity = sum(a * b for a, b in zip(left, right, strict=True))
    return max(0.0, min(1.0, similarity))


class HybridRetriever:
    """Lightweight deterministic baseline with BM25-like and vector retrieval."""

    def __init__(self, snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR) -> None:
        self._chunks = self._load_chunks(snapshot_dir)
        self._tokenized = {item.chunk_id: tokenize(item.content) for item in self._chunks}
        self._vectors = {
            item.chunk_id: hashing_vector(self._tokenized[item.chunk_id]) for item in self._chunks
        }
        self._document_frequency = self._build_document_frequency()
        self._average_length = sum(len(tokens) for tokens in self._tokenized.values()) / max(
            len(self._chunks), 1
        )

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    @staticmethod
    def _load_chunks(snapshot_dir: Path) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        for path in sorted(snapshot_dir.glob("*.json")):
            documents = json.loads(path.read_text(encoding="utf-8"))
            for raw in documents:
                document = KnowledgeDocument.model_validate(raw)
                chunks.append(
                    KnowledgeChunk(
                        chunk_id=f"{document.document_id}:0001",
                        document_id=document.document_id,
                        title=document.title,
                        source_uri=document.source_uri,
                        version=document.version,
                        source_quality=document.source_quality,
                        access_labels=document.access_labels,
                        metadata=document.metadata,
                        content=document.content,
                    )
                )
        return chunks

    def _build_document_frequency(self) -> Counter[str]:
        frequency: Counter[str] = Counter()
        for tokens in self._tokenized.values():
            frequency.update(set(tokens))
        return frequency

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        allowed_access_labels: set[str] | None = None,
    ) -> list[SearchHit]:
        query_tokens = tokenize(query)
        query_vector = hashing_vector(query_tokens)
        hits: list[SearchHit] = []
        for chunk in self._chunks:
            if allowed_access_labels and not (set(chunk.access_labels) & allowed_access_labels):
                continue
            keyword_score = self._bm25(query_tokens, self._tokenized[chunk.chunk_id])
            semantic_score = cosine_similarity(query_vector, self._vectors[chunk.chunk_id])
            final_score = min(
                1.0,
                0.45 * semantic_score + 0.35 * keyword_score + 0.15 * chunk.source_quality + 0.05,
            )
            hits.append(
                SearchHit(
                    chunk=chunk,
                    keyword_score=round(keyword_score, 4),
                    semantic_score=round(semantic_score, 4),
                    final_score=round(final_score, 4),
                )
            )
        return sorted(hits, key=lambda item: item.final_score, reverse=True)[:top_k]

    def _bm25(self, query_tokens: list[str], document_tokens: list[str]) -> float:
        if not query_tokens or not document_tokens:
            return 0.0
        frequencies = Counter(document_tokens)
        score = 0.0
        k1 = 1.5
        b = 0.75
        document_count = max(len(self._chunks), 1)
        for token in set(query_tokens):
            if token not in frequencies:
                continue
            document_frequency = self._document_frequency[token]
            inverse_frequency = math.log(
                1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            term_frequency = frequencies[token]
            denominator = term_frequency + k1 * (
                1 - b + b * len(document_tokens) / max(self._average_length, 1)
            )
            score += inverse_frequency * term_frequency * (k1 + 1) / denominator
        return min(1.0, score / max(len(set(query_tokens)) * 0.6, 1.0))
