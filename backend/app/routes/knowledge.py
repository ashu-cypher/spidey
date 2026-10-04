"""Phase 3 — knowledge base HTTP API.

  POST   /api/knowledge/upload              multipart file upload (<= 10 MB)
  GET    /api/knowledge/documents            list ingested documents
  GET    /api/knowledge/search?q=&limit=    semantic search over chunks
  DELETE /api/knowledge/documents/{id}      remove document + chunks + file

No tracebacks reach users: every failure becomes an HTTPException with a
clean, user-safe message.
"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.rag.pipeline import (
    MAX_UPLOAD_BYTES,
    delete_document,
    ingest_document,
    list_documents,
)
from app.rag.vectorstore import get_vector_store
from app.tools.base import ToolError

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.post("/upload")
async def upload_document(request: Request, file: UploadFile = File(...)):
    # Early reject on declared size before reading the body into memory.
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_UPLOAD_BYTES + 1024 * 1024:
                raise HTTPException(413, "File is too large — the limit is 10 MB.")
        except ValueError:
            pass
    try:
        data = await file.read()
    except Exception:
        raise HTTPException(400, "Couldn't read the uploaded file.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is too large — the limit is 10 MB.")
    if not data:
        raise HTTPException(422, "The uploaded file is empty.")
    try:
        document = ingest_document(
            data,
            filename=file.filename or "upload",
            content_type=file.content_type,
        )
    except ToolError as exc:
        # 422 for validation problems (bad type / unreadable), else 400.
        msg = exc.user_message
        status = 422 if "Unsupported file type" in msg else 400
        raise HTTPException(status, msg)
    return {"document": document}


@router.get("/documents")
async def get_documents():
    return {"documents": list_documents()}


@router.get("/search")
async def search_documents(q: str = "", limit: int = 5):
    if not q.strip():
        raise HTTPException(422, "Search needs a query (?q=...).")
    limit = max(1, min(20, limit))
    from app.rag.embeddings import get_embedding_provider

    query_vec = get_embedding_provider().embed([q.strip()])[0]
    results = get_vector_store().search(
        query_vec, limit=limit, filters={"user_id": "local"}
    )
    return {"results": results}


@router.delete("/documents/{document_id}")
async def remove_document(document_id: str):
    try:
        return delete_document(document_id)
    except ToolError as exc:
        raise HTTPException(404, exc.user_message)


@router.get("/graph")
async def get_graph():
    """Lightweight view of the personal knowledge graph for the SVG widget.

    Returns {"entities": [{id, type, name}], "relations": [{from, to, relation}]}
    with relations deduplicated across entities.
    """
    from app.services.knowledge_graph import find_entities, relations_for

    entities = find_entities()  # newest first, limit 50
    seen: set[tuple[str, str, str]] = set()
    relations = []
    for entity in entities:
        for rel in relations_for(entity["name"]):
            key = (rel["subject"], rel["object"], rel["relation"])
            if key not in seen:
                seen.add(key)
                relations.append(
                    {"from": rel["subject"], "to": rel["object"],
                     "relation": rel["relation"]}
                )
    return {
        "entities": [
            {"id": e["id"], "type": e["type"], "name": e["name"]}
            for e in entities
        ],
        "relations": relations,
    }
