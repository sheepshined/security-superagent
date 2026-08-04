import csv
import hashlib
import io
import json
import os
import re
import tarfile
import zipfile
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

from app.rag.models import KnowledgeDocument, KnowledgeSource, SourceFetchRecord

MAX_SOURCE_BYTES = 200 * 1024 * 1024
MAX_DOCUMENT_CHARS = 18_000


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:128]


class KnowledgeDownloadError(RuntimeError):
    pass


class KnowledgeDownloader:
    def __init__(self, cache_root: Path, *, timeout_seconds: float = 90.0) -> None:
        self.cache_root = cache_root.resolve()
        self.timeout_seconds = timeout_seconds
        self.cache_root.mkdir(parents=True, exist_ok=True)

    def fetch(self, source: KnowledgeSource) -> SourceFetchRecord:
        if not source.uri.startswith(("https://", "http://")):
            raise KnowledgeDownloadError("only HTTP(S) sources can be downloaded")
        extension = Path(source.uri.split("?", 1)[0]).suffix or ".data"
        if source.uri.endswith(".tar.gz"):
            extension = ".tar.gz"
        destination = self.cache_root / (
            f"{_safe_name(source.source_id)}-{_safe_name(source.version)}{extension}"
        )
        temporary = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        byte_count = 0
        try:
            with httpx.stream(
                "GET",
                source.uri,
                follow_redirects=True,
                timeout=self.timeout_seconds,
                headers={"User-Agent": "Security-SuperAgent-Knowledge-Indexer/1.0"},
            ) as response:
                response.raise_for_status()
                with temporary.open("wb") as target:
                    for block in response.iter_bytes():
                        byte_count += len(block)
                        if byte_count > MAX_SOURCE_BYTES:
                            raise KnowledgeDownloadError("source exceeds the 200 MB safety limit")
                        digest.update(block)
                        target.write(block)
            actual_hash = digest.hexdigest()
            if source.sha256 and actual_hash != source.sha256:
                raise KnowledgeDownloadError("download hash does not match the source registry")
            os.replace(temporary, destination)
        except Exception:
            if temporary.exists():
                temporary.unlink()
            raise

        fetched_at = datetime.now(UTC)
        record = SourceFetchRecord(
            source_id=source.source_id,
            version=source.version,
            uri=source.uri,
            license=source.license,
            fetched_at=fetched_at,
            content_hash=actual_hash,
            byte_count=byte_count,
            cache_path=str(destination),
        )
        manifest = destination.with_suffix(destination.suffix + ".manifest.json")
        manifest.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return record


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "nav", "footer"}:
            self._ignored_depth += 1
        elif tag in {"p", "br", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "footer"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag in {"p", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts)
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", value)).strip()


class KnowledgeParser:
    def parse(
        self,
        source: KnowledgeSource,
        path: Path,
        *,
        fetched_at: datetime,
    ) -> list[KnowledgeDocument]:
        if source.source_type == "stix":
            documents = self._parse_stix(source, path, fetched_at)
        elif source.source_type == "json":
            documents = self._parse_json(source, path, fetched_at)
        elif source.source_type == "csv":
            documents = self._parse_csv(source, path, fetched_at)
        elif source.source_type == "yaml":
            documents = self._parse_yaml(source, path, fetched_at)
        elif source.source_type == "html":
            parser = _VisibleTextParser()
            parser.feed(path.read_text(encoding="utf-8", errors="replace"))
            documents = self._text_documents(source, source.name, parser.text(), fetched_at)
        elif source.source_type == "pdf":
            documents = self._parse_pdf(source, path, fetched_at)
        else:
            raise ValueError(f"unsupported downloaded source type: {source.source_type}")
        return [item for item in documents if item.content.strip()]

    def _base_document(
        self,
        source: KnowledgeSource,
        *,
        document_id: str,
        title: str,
        content: str,
        fetched_at: datetime,
        metadata: dict[str, Any] | None = None,
        published_at: datetime | None = None,
    ) -> KnowledgeDocument:
        normalized = content.strip()[:20_000]
        timestamp = published_at or fetched_at
        return KnowledgeDocument(
            document_id=document_id[:128],
            title=title[:512],
            source_uri=source.uri,
            version=source.version,
            published_at=timestamp,
            effective_at=timestamp,
            source_quality=1.0,
            license=source.license,
            fetched_at=fetched_at,
            content_hash=content_hash(normalized),
            parent_document_id=source.source_id,
            source_type=source.source_type,
            access_labels=source.access_labels,
            metadata={"source_id": source.source_id, **(metadata or {})},
            content=normalized,
        )

    def _text_documents(
        self,
        source: KnowledgeSource,
        title: str,
        text: str,
        fetched_at: datetime,
    ) -> list[KnowledgeDocument]:
        sections = [
            text[index : index + MAX_DOCUMENT_CHARS]
            for index in range(0, len(text), MAX_DOCUMENT_CHARS)
        ]
        return [
            self._base_document(
                source,
                document_id=f"{source.source_id}-{index:04d}",
                title=title if len(sections) == 1 else f"{title}（第 {index} 部分）",
                content=section,
                fetched_at=fetched_at,
                metadata={"section": index},
            )
            for index, section in enumerate(sections, start=1)
            if section.strip()
        ]

    def _parse_stix(
        self, source: KnowledgeSource, path: Path, fetched_at: datetime
    ) -> list[KnowledgeDocument]:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        objects = bundle.get("objects", [])
        names = {str(item.get("id")): str(item.get("name", item.get("id", ""))) for item in objects}
        documents: list[KnowledgeDocument] = []
        allowed_types = {
            "attack-pattern",
            "course-of-action",
            "x-mitre-data-source",
            "x-mitre-data-component",
            "relationship",
        }
        for item in objects:
            object_type = str(item.get("type", ""))
            if (
                object_type not in allowed_types
                or item.get("revoked")
                or item.get("x_mitre_deprecated")
            ):
                continue
            external_id = ""
            for reference in item.get("external_references", []):
                if reference.get("source_name") == "mitre-attack":
                    external_id = str(reference.get("external_id", ""))
                    break
            if object_type == "relationship":
                source_name = names.get(
                    str(item.get("source_ref")), str(item.get("source_ref", ""))
                )
                target_name = names.get(
                    str(item.get("target_ref")), str(item.get("target_ref", ""))
                )
                title = f"ATT&CK relationship: {source_name} -> {target_name}"
                description = str(item.get("description", ""))
                relationship_type = item.get("relationship_type", "relates-to")
                body = (
                    f"{source_name} {relationship_type} {target_name}. "
                    f"{description}"
                )
            else:
                title = f"{external_id} - {item.get('name', object_type)}".strip(" -")
                body = str(item.get("description", item.get("name", "")))
            if not body.strip():
                continue
            modified = str(item.get("modified", item.get("created", "")))
            published_at = (
                datetime.fromisoformat(modified.replace("Z", "+00:00")) if modified else fetched_at
            )
            documents.append(
                self._base_document(
                    source,
                    document_id=(
                        f"{source.source_id}-"
                        f"{_safe_name(external_id or str(item.get('id')))}"
                    ),
                    title=title,
                    content=body,
                    fetched_at=fetched_at,
                    published_at=published_at,
                    metadata={
                        "stix_id": item.get("id"),
                        "stix_type": object_type,
                        "technique_id": external_id if external_id.startswith("T") else "",
                        "tactic": ", ".join(
                            phase.get("phase_name", "")
                            for phase in item.get("kill_chain_phases", [])
                        ),
                    },
                )
            )
        return documents

    def _parse_json(
        self, source: KnowledgeSource, path: Path, fetched_at: datetime
    ) -> list[KnowledgeDocument]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if source.parser_options.get("schema") == "cisa-kev":
            return [
                self._base_document(
                    source,
                    document_id=f"cisa-kev-{item['cveID']}",
                    title=f"{item['cveID']} - {item.get('vulnerabilityName', '')}",
                    content=(
                        f"Vendor/project: {item.get('vendorProject', '')}. "
                        f"Product: {item.get('product', '')}. "
                        f"Description: {item.get('shortDescription', '')} Required action: "
                        f"{item.get('requiredAction', '')}. Due date: {item.get('dueDate', '')}."
                    ),
                    fetched_at=fetched_at,
                    metadata={
                        "cve_id": item["cveID"],
                        "date_added": item.get("dateAdded"),
                        "known_ransomware": item.get("knownRansomwareCampaignUse"),
                    },
                )
                for item in payload.get("vulnerabilities", [])
            ]
        return self._text_documents(
            source, source.name, json.dumps(payload, ensure_ascii=False), fetched_at
        )

    def _parse_csv(
        self, source: KnowledgeSource, path: Path, fetched_at: datetime
    ) -> list[KnowledgeDocument]:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                member = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
                text = archive.read(member).decode("utf-8-sig", errors="replace")
        else:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        documents: list[KnowledgeDocument] = []
        for index, row in enumerate(csv.DictReader(io.StringIO(text)), start=1):
            identifier = row.get("CWE-ID") or row.get("ID") or str(index)
            title = row.get("Name") or f"{source.name} {identifier}"
            documents.append(
                self._base_document(
                    source,
                    document_id=f"{source.source_id}-{_safe_name(identifier)}",
                    title=f"CWE-{identifier} - {title}" if identifier.isdigit() else title,
                    content=json.dumps(row, ensure_ascii=False),
                    fetched_at=fetched_at,
                    metadata={"cwe_id": identifier},
                )
            )
        return documents

    def _parse_yaml(
        self, source: KnowledgeSource, path: Path, fetched_at: datetime
    ) -> list[KnowledgeDocument]:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is required to parse Sigma rules") from exc
        filters = [str(value).lower() for value in source.parser_options.get("path_contains", [])]
        entries: list[tuple[str, bytes]] = []
        if tarfile.is_tarfile(path):
            with tarfile.open(path) as archive:
                for member in archive.getmembers():
                    name = member.name.lower()
                    if not member.isfile() or not name.endswith((".yml", ".yaml")):
                        continue
                    handle = archive.extractfile(member)
                    if handle:
                        entries.append((member.name, handle.read()))
        else:
            entries.append((path.name, path.read_bytes()))
        documents: list[KnowledgeDocument] = []
        for name, raw in entries:
            try:
                rule = yaml.safe_load(raw.decode("utf-8", errors="replace"))
            except yaml.YAMLError:
                continue
            if not isinstance(rule, dict) or not rule.get("title"):
                continue
            searchable = json.dumps(
                {
                    "path": name,
                    "title": rule.get("title"),
                    "description": rule.get("description"),
                    "tags": rule.get("tags"),
                    "logsource": rule.get("logsource"),
                    "detection": rule.get("detection"),
                },
                ensure_ascii=False,
                default=str,
            ).lower()
            if filters and not any(value in searchable for value in filters):
                continue
            rule_id = str(rule.get("id", hashlib.sha256(raw).hexdigest()[:16]))
            documents.append(
                self._base_document(
                    source,
                    document_id=f"sigma-{_safe_name(rule_id)}",
                    title=str(rule["title"]),
                    content=json.dumps(rule, ensure_ascii=False, default=str),
                    fetched_at=fetched_at,
                    metadata={
                        "sigma_id": rule_id,
                        "level": rule.get("level"),
                        "status": rule.get("status"),
                        "path": name,
                        "tags": rule.get("tags", []),
                    },
                )
            )
        return documents

    def _parse_pdf(
        self, source: KnowledgeSource, path: Path, fetched_at: datetime
    ) -> list[KnowledgeDocument]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("pypdf is required to parse legal PDF sources") from exc
        reader = PdfReader(path)
        text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)
        return self._text_documents(source, source.name, text, fetched_at)
