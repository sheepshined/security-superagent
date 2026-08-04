import hashlib
import json
import math
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.rag.models import KnowledgeChunk, KnowledgeDocument, SearchHit

DEFAULT_SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "knowledge" / "snapshots"
TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_.-]+|[\u4e00-\u9fff]+")
TECHNIQUE_PATTERN = re.compile(r"\bT\d{4}(?:\.\d{3})?\b", re.IGNORECASE)
RRF_K = 60


class SemanticProvider(Protocol):
    def score(self, query: str, chunks: list[KnowledgeChunk]) -> dict[str, float]: ...


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
    """Access-controlled BM25/vector retrieval with RRF and deterministic fallback."""

    def __init__(
        self,
        snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR,
        *,
        chunks: list[KnowledgeChunk] | None = None,
        semantic_provider: SemanticProvider | None = None,
    ) -> None:
        self._chunks = chunks if chunks is not None else self._load_chunks(snapshot_dir)
        self._chunks = [item for item in self._chunks if item.content and item.source_uri]
        self._semantic_provider = semantic_provider
        self._tokenized = {item.chunk_id: tokenize(item.content) for item in self._chunks}
        self._vectors = (
            {}
            if semantic_provider
            else {
                item.chunk_id: hashing_vector(self._tokenized[item.chunk_id])
                for item in self._chunks
            }
        )
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
                if document.deleted:
                    continue
                content_hash = (
                    document.content_hash
                    or hashlib.sha256(document.content.encode("utf-8")).hexdigest()
                )
                chunks.append(
                    KnowledgeChunk(
                        chunk_id=f"{document.document_id}:0001",
                        document_id=document.document_id,
                        title=document.title,
                        source_uri=document.source_uri,
                        version=document.version,
                        source_quality=document.source_quality,
                        license=document.license,
                        fetched_at=document.fetched_at,
                        content_hash=content_hash,
                        parent_document_id=(document.parent_document_id or document.document_id),
                        source_type=document.source_type,
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
        scenario: str | None = None,
    ) -> list[SearchHit]:
        query_tokens = tokenize(query)
        query_vector = hashing_vector(query_tokens)
        eligible = [
            chunk
            for chunk in self._chunks
            if not allowed_access_labels or bool(set(chunk.access_labels) & allowed_access_labels)
        ]
        keyword_scores = {
            chunk.chunk_id: self._bm25(query_tokens, self._tokenized[chunk.chunk_id])
            for chunk in eligible
        }
        if self._semantic_provider:
            semantic_scores = self._semantic_provider.score(query, eligible)
        else:
            semantic_scores = {
                chunk.chunk_id: cosine_similarity(query_vector, self._vectors[chunk.chunk_id])
                for chunk in eligible
            }

        keyword_ranks = self._rank(keyword_scores)
        semantic_ranks = self._rank(semantic_scores)
        query_techniques = {item.upper() for item in TECHNIQUE_PATTERN.findall(query)}
        scored: list[SearchHit] = []
        for chunk in eligible:
            chunk_id = chunk.chunk_id
            rrf = (1 / (RRF_K + keyword_ranks[chunk_id])) + (1 / (RRF_K + semantic_ranks[chunk_id]))
            normalized_rrf = rrf / (2 / (RRF_K + 1))
            technique_id = str(chunk.metadata.get("technique_id", "")).upper()
            technique_boost = 0.22 if technique_id in query_techniques else 0.0
            source_boost = 0.08 * chunk.source_quality
            recency_boost = 0.03 * self._recency_score(chunk.fetched_at)
            scenario_boost = 0.03 if scenario and self._matches_scenario(chunk, scenario) else 0.0
            domain_boost = self._domain_boost(query, chunk)
            final_score = min(
                1.0,
                max(
                    0.0,
                    0.65 * normalized_rrf
                    + technique_boost
                    + source_boost
                    + recency_boost
                    + scenario_boost
                    + domain_boost,
                ),
            )
            scored.append(
                SearchHit(
                    chunk=chunk,
                    keyword_score=round(keyword_scores.get(chunk_id, 0.0), 4),
                    semantic_score=round(semantic_scores.get(chunk_id, 0.0), 4),
                    final_score=round(final_score, 4),
                )
            )

        deduplicated: list[SearchHit] = []
        seen_hashes: set[str] = set()
        seen_documents: set[str] = set()
        for hit in sorted(scored, key=lambda item: item.final_score, reverse=True):
            if hit.chunk.content_hash in seen_hashes or hit.chunk.document_id in seen_documents:
                continue
            seen_hashes.add(hit.chunk.content_hash)
            seen_documents.add(hit.chunk.document_id)
            deduplicated.append(hit)
        return deduplicated[:top_k]

    @staticmethod
    def _rank(scores: dict[str, float]) -> dict[str, int]:
        ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
        ranks: dict[str, int] = {}
        last_score: float | None = None
        last_rank = 0
        for index, chunk_id in enumerate(ordered, start=1):
            score = scores[chunk_id]
            if last_score is None or not math.isclose(score, last_score, abs_tol=1e-12):
                last_rank = index
                last_score = score
            ranks[chunk_id] = last_rank
        return ranks

    @staticmethod
    def _recency_score(fetched_at: datetime | None) -> float:
        if fetched_at is None:
            return 0.0
        value = fetched_at if fetched_at.tzinfo else fetched_at.replace(tzinfo=UTC)
        age_days = max(0.0, (datetime.now(UTC) - value).total_seconds() / 86400)
        return max(0.0, 1.0 - age_days / 1095)

    @staticmethod
    def _matches_scenario(chunk: KnowledgeChunk, scenario: str) -> bool:
        normalized = scenario.lower().replace("-", "_")
        candidates = {
            str(chunk.metadata.get("scenario", "")).lower().replace("-", "_"),
            str(chunk.metadata.get("tactic", "")).lower().replace(" ", "_"),
        }
        return normalized in candidates

    @staticmethod
    def _domain_boost(query: str, chunk: KnowledgeChunk) -> float:
        normalized = query.lower()
        source_id = str(chunk.metadata.get("source_id", ""))
        stix_type = str(chunk.metadata.get("stix_type", ""))
        boost = 0.04 if stix_type == "attack-pattern" else 0.0
        if stix_type == "relationship":
            boost -= 0.08

        cue_groups = {
            "cisa-kev": ("cve", "kev", "known exploited", "在野利用", "修复期限"),
            "sigma-lateral-movement": ("sigma", "检测规则", "detection rule"),
            "cn-personal-information-protection-law": (
                "个人信息",
                "personal information",
                "最小必要",
            ),
            "cn-data-security-law": ("数据安全法", "data security law"),
            "cn-network-data-security-regulation": ("网络数据", "network data"),
            "cn-cii-security-regulation": (
                "关键信息基础设施",
                "critical information infrastructure",
            ),
            "cn-cybersecurity-law": ("网络安全法", "cybersecurity law"),
        }
        cues = cue_groups.get(source_id, ())
        if any(cue in normalized for cue in cues):
            boost += 0.18 if source_id == "cisa-kev" else 0.14
        return boost

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
