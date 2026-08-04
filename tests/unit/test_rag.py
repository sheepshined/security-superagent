import io
import json
import tarfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from app.rag.catalog import KnowledgeCatalog, KnowledgeCatalogError
from app.rag.hybrid import HybridRetriever
from app.rag.index import KnowledgeIndexManager, documents_to_chunks
from app.rag.models import KnowledgeDocument, KnowledgeSource
from app.rag.pipeline import KnowledgeDownloader, KnowledgeDownloadError, KnowledgeParser


def test_hybrid_retriever_returns_smb_attack_knowledge() -> None:
    retriever = HybridRetriever()

    hits = retriever.search(
        "异常 SMB 管理共享横向移动和有效账号登录",
        top_k=3,
        allowed_access_labels={"public", "synthetic"},
    )

    assert hits
    assert any(hit.chunk.metadata.get("technique_id") == "T1021.002" for hit in hits)
    assert all(0 <= hit.final_score <= 1 for hit in hits)


def test_hybrid_retriever_enforces_access_labels() -> None:
    retriever = HybridRetriever()

    hits = retriever.search("横向移动", allowed_access_labels={"restricted"})

    assert hits == []


def _document(document_id: str, content: str) -> KnowledgeDocument:
    now = datetime.now(UTC)
    return KnowledgeDocument(
        document_id=document_id,
        title=f"Document {document_id}",
        source_uri=f"https://example.test/{document_id}",
        version="1.0",
        published_at=now,
        effective_at=now,
        source_quality=1.0,
        license="test-license",
        fetched_at=now,
        parent_document_id=document_id,
        source_type="snapshot",
        access_labels=["public"],
        metadata={"source_id": "test-source"},
        content=content,
    )


def test_chunking_deduplicates_identical_content_and_preserves_provenance() -> None:
    chunks = documents_to_chunks(
        [_document("doc-1", "same security guidance"), _document("doc-2", "same security guidance")]
    )

    assert len(chunks) == 1
    assert chunks[0].license == "test-license"
    assert len(chunks[0].content_hash) == 64
    assert chunks[0].parent_document_id == "doc-1"


def test_index_build_is_versioned_and_propagates_deletions(tmp_path: Path) -> None:
    registry = tmp_path / "sources.json"
    registry.write_text("[]", encoding="utf-8")
    catalog = KnowledgeCatalog(tmp_path / "indexes", registry)
    manager = KnowledgeIndexManager(catalog, model_cache=tmp_path / "models")

    first = manager.build(
        [_document("doc-1", "alpha"), _document("doc-2", "beta")],
        index_id="index-v1",
        enable_chroma=False,
    )
    second = manager.build(
        [_document("doc-2", "beta")],
        index_id="index-v2",
        enable_chroma=False,
    )

    assert first.status == "ready"
    assert second.deleted_document_ids == ["doc-1"]
    assert catalog.active_index_id == "index-v2"
    assert manager.create_retriever().search("beta")[0].chunk.document_id == "doc-2"


def test_catalog_rejects_unknown_or_unsafe_activation(tmp_path: Path) -> None:
    registry = tmp_path / "sources.json"
    registry.write_text("[]", encoding="utf-8")
    catalog = KnowledgeCatalog(tmp_path / "indexes", registry)

    with pytest.raises(KnowledgeCatalogError):
        catalog.activate("../outside")


def test_cisa_kev_parser_creates_traceable_documents(tmp_path: Path) -> None:
    source = KnowledgeSource(
        source_id="cisa-kev",
        name="CISA KEV",
        source_type="json",
        uri="https://example.test/kev.json",
        version="test",
        license="public",
        license_uri="https://example.test/license",
        parser_options={"schema": "cisa-kev"},
    )
    payload = {
        "vulnerabilities": [
            {
                "cveID": "CVE-2026-0001",
                "vendorProject": "Example",
                "product": "Widget",
                "vulnerabilityName": "Example issue",
                "shortDescription": "Known exploitation.",
                "requiredAction": "Apply mitigations.",
                "dueDate": "2026-09-01",
            }
        ]
    }
    path = tmp_path / "kev.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    documents = KnowledgeParser().parse(source, path, fetched_at=datetime.now(UTC))

    assert documents[0].document_id == "cisa-kev-CVE-2026-0001"
    assert documents[0].metadata["cve_id"] == "CVE-2026-0001"
    assert documents[0].license == "public"


def test_sigma_parser_skips_bad_yaml_without_dropping_valid_rules(tmp_path: Path) -> None:
    source = KnowledgeSource(
        source_id="sigma-lateral-movement",
        name="Sigma lateral movement",
        source_type="yaml",
        uri="https://example.test/sigma.tar.gz",
        version="test",
        license="DRL 1.1",
        license_uri="https://example.test/license",
        parser_options={"path_contains": ["psexec"]},
    )
    archive_path = tmp_path / "sigma.tar.gz"
    valid = b"title: PsExec Remote Service\nid: test-rule\nstatus: test\n"
    invalid = b"title: [unterminated"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, content in (("rules/psexec.yml", valid), ("rules/bad.yml", invalid)):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))

    documents = KnowledgeParser().parse(
        source,
        archive_path,
        fetched_at=datetime.now(UTC),
    )

    assert [item.document_id for item in documents] == ["sigma-test-rule"]


def _source(source_type: str, *, source_id: str = "test-source") -> KnowledgeSource:
    return KnowledgeSource(
        source_id=source_id,
        name="Test source",
        source_type=source_type,
        uri=f"https://example.test/{source_id}.{source_type}",
        version="1",
        license="test-license",
        license_uri="https://example.test/license",
    )


def test_stix_html_json_and_csv_parsers_cover_supported_formats(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    stix_path = tmp_path / "attack.json"
    stix_path.write_text(
        json.dumps(
            {
                "objects": [
                    {
                        "type": "attack-pattern",
                        "id": "attack-pattern--one",
                        "name": "Remote Services",
                        "description": "Remote service execution.",
                        "modified": "2026-01-01T00:00:00Z",
                        "external_references": [
                            {
                                "source_name": "mitre-attack",
                                "external_id": "T1021",
                            }
                        ],
                        "kill_chain_phases": [{"phase_name": "lateral-movement"}],
                    },
                    {
                        "type": "relationship",
                        "id": "relationship--one",
                        "source_ref": "attack-pattern--one",
                        "target_ref": "course-of-action--one",
                        "relationship_type": "mitigated-by",
                        "description": "Restrict remote services.",
                    },
                    {
                        "type": "attack-pattern",
                        "id": "attack-pattern--revoked",
                        "name": "Old",
                        "revoked": True,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    stix_docs = KnowledgeParser().parse(_source("stix"), stix_path, fetched_at=now)
    assert len(stix_docs) == 2
    assert stix_docs[0].metadata["technique_id"] == "T1021"
    assert "relationship" in stix_docs[1].title

    html_path = tmp_path / "law.html"
    html_path.write_text(
        "<html><nav>ignore</nav><h1>Security Law</h1><p>Protect systems.</p>"
        "<script>ignore()</script></html>",
        encoding="utf-8",
    )
    html_docs = KnowledgeParser().parse(_source("html"), html_path, fetched_at=now)
    assert "Protect systems" in html_docs[0].content
    assert "ignore" not in html_docs[0].content

    json_path = tmp_path / "generic.json"
    json_path.write_text('{"guidance":"retain evidence"}', encoding="utf-8")
    assert (
        "retain evidence"
        in KnowledgeParser().parse(_source("json"), json_path, fetched_at=now)[0].content
    )

    csv_zip = tmp_path / "cwe.zip"
    with zipfile.ZipFile(csv_zip, "w") as archive:
        archive.writestr("top.csv", "CWE-ID,Name\n79,Cross-site Scripting\n")
    csv_docs = KnowledgeParser().parse(_source("csv"), csv_zip, fetched_at=now)
    assert csv_docs[0].document_id == "test-source-79"
    assert csv_docs[0].title.startswith("CWE-79")


def test_downloader_writes_hash_manifest_and_cleans_failed_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"approved knowledge payload"

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield payload[:8]
            yield payload[8:]

    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: FakeResponse())
    downloader = KnowledgeDownloader(tmp_path / "cache")
    source = _source("json", source_id="download-source")
    record = downloader.fetch(source)

    assert Path(record.cache_path).read_bytes() == payload
    assert record.byte_count == len(payload)
    assert Path(record.cache_path + ".manifest.json").is_file()

    bad_source = source.model_copy(update={"sha256": "0" * 64, "version": "bad"})
    with pytest.raises(KnowledgeDownloadError, match="hash"):
        downloader.fetch(bad_source)
    assert not list((tmp_path / "cache").glob("*.part"))


def test_parser_rejects_unsupported_downloaded_source_type(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.data"
    path.write_text("content", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported"):
        KnowledgeParser().parse(_source("snapshot"), path, fetched_at=datetime.now(UTC))
