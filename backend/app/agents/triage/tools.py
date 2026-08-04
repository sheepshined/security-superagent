from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rag.hybrid import HybridRetriever


class KnowledgeSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=12000)
    top_k: int = Field(default=4, ge=1, le=10)


def create_knowledge_search_tool(retriever: HybridRetriever) -> StructuredTool:
    def search_security_knowledge(query: str, top_k: int = 4) -> list[dict[str, Any]]:
        """Search approved security knowledge and return access-controlled chunks."""
        hits = retriever.search(
            query,
            top_k=top_k,
            allowed_access_labels={"public", "synthetic"},
        )
        return [
            {
                "document_id": hit.chunk.document_id,
                "chunk_id": hit.chunk.chunk_id,
                "title": hit.chunk.title,
                "source_uri": hit.chunk.source_uri,
                "version": hit.chunk.version,
                "content": hit.chunk.content,
                "final_score": hit.final_score,
            }
            for hit in hits
        ]

    return StructuredTool.from_function(
        func=search_security_knowledge,
        name="search_security_knowledge",
        description=(
            "检索经过访问标签过滤的安全知识库，返回可引用的文档片段；"
            "本工具只读，不执行安全处置。"
        ),
        args_schema=KnowledgeSearchInput,
    )
