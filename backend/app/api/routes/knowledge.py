from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_retriever
from app.rag.hybrid import HybridRetriever
from app.rag.models import SearchHit

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
RetrieverDependency = Annotated[HybridRetriever, Depends(get_retriever)]


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
