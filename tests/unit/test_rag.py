from app.rag.hybrid import HybridRetriever


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
