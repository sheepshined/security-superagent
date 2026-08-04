from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import (
    get_knowledge_catalog,
    get_retriever,
    require_admin,
    require_csrf,
)
from app.rag.catalog import KnowledgeCatalog, KnowledgeCatalogError
from app.rag.hybrid import HybridRetriever
from app.rag.models import (
    KnowledgeIndexManifest,
    KnowledgeMetrics,
    KnowledgeSource,
    SearchHit,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
RetrieverDependency = Annotated[HybridRetriever, Depends(get_retriever)]
CatalogDependency = Annotated[KnowledgeCatalog, Depends(get_knowledge_catalog)]


@router.get("/search", response_model=list[SearchHit])
def search_knowledge(
    retriever: RetrieverDependency,
    q: str = Query(min_length=2, max_length=1000),
    top_k: int = Query(default=5, ge=1, le=10),
) -> list[SearchHit]:
    return retriever.search(
        q,
        top_k=top_k,
        allowed_access_labels={"public", "synthetic"},
    )


@router.get("/indexes", response_model=list[KnowledgeIndexManifest])
def list_indexes(catalog: CatalogDependency) -> list[KnowledgeIndexManifest]:
    return catalog.list_indexes()


@router.get("/sources", response_model=list[KnowledgeSource])
def list_sources(catalog: CatalogDependency) -> list[KnowledgeSource]:
    return catalog.list_sources()


@router.get("/metrics", response_model=KnowledgeMetrics)
def knowledge_metrics(catalog: CatalogDependency) -> KnowledgeMetrics:
    return catalog.metrics()


@router.post(
    "/indexes/{index_id}/activate",
    response_model=KnowledgeIndexManifest,
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def activate_index(index_id: str, catalog: CatalogDependency) -> KnowledgeIndexManifest:
    try:
        return catalog.activate(index_id)
    except KnowledgeCatalogError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
