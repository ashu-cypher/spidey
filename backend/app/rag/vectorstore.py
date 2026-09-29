"""Phase 3 — vector store abstraction.

Two backends behind one interface:

* ``PgVectorStore`` — cosine-distance ordering in PostgreSQL via
  ``pgvector.sqlalchemy`` (``embedding <=> :vec``), used when
  ``VECTOR_BACKEND=pgvector``.
* ``LocalVectorStore`` — brute-force cosine similarity in Python; works on any
  SQL backend (sqlite JSON embeddings included).

Scores are cosine *similarities* (0..1, higher = more relevant) on both
backends: every embedding provider L2-normalizes its vectors, so
``score = 1 - cosine_distance`` is exactly the cosine similarity.
"""
from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod

from sqlalchemy import select

from app.config import settings
from app.database import get_session
from app.models import Document, DocumentChunk

logger = logging.getLogger(__name__)


def _cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a[:n], b[:n]))
    na = math.sqrt(sum(x * x for x in a[:n]))
    nb = math.sqrt(sum(y * y for y in b[:n]))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _as_list(vector) -> list[float]:
    if vector is None:
        return []
    if isinstance(vector, list):
        return [float(v) for v in vector]
    try:  # numpy array, pgvector Vector, tuples…
        return [float(v) for v in list(vector)]
    except TypeError:
        return []


class VectorStore(ABC):
    """Nearest-neighbor search over document chunk embeddings."""

    @abstractmethod
    def search(
        self,
        query_embedding: list[float],
        limit: int = 5,
        filters: dict | None = None,
    ) -> list[dict]:
        """Return top-``limit`` hits as dicts with chunk, document and score.

        Each hit: ``{"chunk_id", "chunk_index", "content", "document_id",
        "source", "created_at", "score"}`` where ``score`` is cosine
        similarity (higher = more relevant).
        """
        raise NotImplementedError


class PgVectorStore(VectorStore):
    """pgvector-backed search: ``ORDER BY embedding <=> :vec``."""

    def search(
        self,
        query_embedding: list[float],
        limit: int = 5,
        filters: dict | None = None,
    ) -> list[dict]:
        filters = filters or {}
        with get_session() as session:
            distance = DocumentChunk.embedding.cosine_distance(query_embedding)
            stmt = (
                select(DocumentChunk, Document, distance.label("distance"))
                .join(Document, DocumentChunk.document_id == Document.id)
                .order_by(distance)
                .limit(max(1, limit))
            )
            if filters.get("user_id"):
                stmt = stmt.where(Document.user_id == filters["user_id"])
            if filters.get("document_id"):
                stmt = stmt.where(DocumentChunk.document_id == filters["document_id"])
            hits: list[dict] = []
            for chunk, doc, dist in session.execute(stmt).all():
                hits.append(_hit(chunk, doc, 1.0 - float(dist)))
            return hits


class LocalVectorStore(VectorStore):
    """Brute-force cosine similarity in Python (any SQL backend)."""

    def search(
        self,
        query_embedding: list[float],
        limit: int = 5,
        filters: dict | None = None,
    ) -> list[dict]:
        filters = filters or {}
        with get_session() as session:
            stmt = select(DocumentChunk, Document).join(
                Document, DocumentChunk.document_id == Document.id
            )
            if filters.get("user_id"):
                stmt = stmt.where(Document.user_id == filters["user_id"])
            if filters.get("document_id"):
                stmt = stmt.where(DocumentChunk.document_id == filters["document_id"])
            scored: list[tuple[float, DocumentChunk, Document]] = []
            for chunk, doc in session.execute(stmt).all():
                vec = _as_list(chunk.embedding)
                if not vec:
                    continue
                scored.append((_cosine(query_embedding, vec), chunk, doc))
            scored.sort(key=lambda t: t[0], reverse=True)
            return [
                _hit(chunk, doc, score)
                for score, chunk, doc in scored[: max(1, limit)]
            ]


def _hit(chunk: DocumentChunk, doc: Document, score: float) -> dict:
    return {
        "chunk_id": chunk.id,
        "chunk_index": chunk.chunk_index,
        "content": chunk.content,
        "document_id": doc.id,
        "source": doc.filename or doc.title,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "score": round(float(score), 4),
    }


def get_vector_store() -> VectorStore:
    """Factory: pgvector when configured, local fallback otherwise."""
    if settings.vector_backend == "pgvector":
        logger.info("Vector store ACTIVE: pgvector (SQL cosine distance)")
        return PgVectorStore()
    logger.info("Vector store ACTIVE: local (Python cosine similarity)")
    return LocalVectorStore()
