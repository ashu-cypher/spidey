"""Phase 3 — RAG tool: semantic search over the user's uploaded documents.

Actions:
  search         {"action": "search", "query": str, "limit"?: int}
                 -> {"results": [{"document_id", "chunk_id", "chunk_index",
                     "content", "score", "source", "created_at"}]}
  list_documents {"action": "list_documents"}
                 -> {"documents": [...], "results": [...]}  ("results" alias
                     kept so agent verification keys match either action)
"""
from __future__ import annotations

from app.rag.embeddings import get_embedding_provider
from app.rag.pipeline import list_documents
from app.rag.vectorstore import get_vector_store
from app.tools.base import BaseTool, ToolError


class RAGTool(BaseTool):
    name = "rag"
    description = (
        "Searches the user's uploaded documents (the knowledge base) for "
        "information relevant to a query. Use for questions about document "
        "contents; do NOT use it for personal memories (use the memory tool)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "query": {"type": "string"},
            "limit": {"type": "integer"},
            "document_id": {"type": "string"},
        },
        "required": ["action"],
    }
    output_schema = {"results": "list"}

    async def _run(self, **kwargs) -> dict:
        action = kwargs.get("action")
        if action == "search":
            return self._search(kwargs)
        if action == "list_documents":
            docs = list_documents(kwargs.get("user_id") or "local")
            return {"documents": docs, "results": docs}
        raise ToolError(f"Unknown rag action: {action!r}.")

    def _search(self, kwargs: dict) -> dict:
        query = (kwargs.get("query") or "").strip()
        if not query:
            raise ToolError("RAG search needs a query.")
        try:
            limit = max(1, min(20, int(kwargs.get("limit") or 5)))
        except (TypeError, ValueError):
            limit = 5
        provider = get_embedding_provider()
        query_vec = provider.embed([query])[0]
        filters = {"user_id": kwargs.get("user_id") or "local"}
        if kwargs.get("document_id"):
            filters["document_id"] = kwargs["document_id"]
        results = get_vector_store().search(query_vec, limit=limit, filters=filters)
        return {"results": results, "query": query}
