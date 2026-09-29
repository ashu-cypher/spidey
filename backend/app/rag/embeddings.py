"""Phase 3 — embedding providers.

The factory :func:`get_embedding_provider` prefers a real semantic model
(``sentence-transformers`` / ``all-MiniLM-L6-v2``) when it is installed and
falls back to :class:`HashingEmbeddingProvider` otherwise. The active provider
is logged once at startup so there is never any doubt about which one the
knowledge base is using.

Dimension contract: every provider in this module exposes ``dim == 384``.
The ``document_chunks.embedding`` column is ``Vector(384)`` on the pgvector
backend and JSON on the local/sqlite backend, so the embedding dimension and
the DB column dimension must never drift apart.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384


class EmbeddingProvider(ABC):
    """Abstract embedding provider: deterministic vectors of fixed dimension."""

    name: str = "base"

    @property
    @abstractmethod
    def dim(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one L2-normalized vector per input text."""
        raise NotImplementedError


class SentenceTransformerProvider(EmbeddingProvider):
    """Real semantic embeddings via sentence-transformers.

    Only instantiated when the ``sentence-transformers`` package is importable;
    ``get_embedding_provider()`` decides that at runtime so this module never
    hard-fails on machines without torch installed.
    """

    name = "sentence-transformers"

    def __init__(self, model) -> None:
        self._model = model

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(
            texts, normalize_embeddings=True, convert_to_numpy=True
        )
        return [list(map(float, v)) for v in vectors]


_TOKEN_RE = re.compile(r"[a-z0-9]+")


class HashingEmbeddingProvider(EmbeddingProvider):
    """DEV ONLY — not semantic.

    Deterministic char-trigram hashing vectors (the "hashing trick"). Lexical
    overlap between query and chunk raises the cosine score, so keyword-style
    retrieval works, but this provider has *no understanding of meaning*:
    paraphrases and synonyms will not match. Never use it for a production
    knowledge base; it exists so the RAG pipeline, vector store, agent wiring
    and tests can run without downloading torch + a transformer model.
    """

    name = "hashing-dev-fallback"

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * EMBEDDING_DIM
        tokens = _TOKEN_RE.findall(text.lower())
        # Character trigrams over the token stream: adjacent-word pairs keep a
        # little local context, single words keep keyword hits strong.
        grams: list[str] = []
        for tok in tokens:
            padded = f"^{tok}$"
            grams.extend(padded[i : i + 3] for i in range(len(padded) - 2))
        grams.extend(f"{a} {b}" for a, b in zip(tokens, tokens[1:]))
        if not grams:
            grams = ["<empty>"]
        for gram in grams:
            digest = hashlib.md5(gram.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % EMBEDDING_DIM
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    """Return the active provider, preferring sentence-transformers.

    The choice is logged once (at first use) so startup output always shows
    which embedding backend the knowledge base is running on.
    """
    global _provider
    if _provider is not None:
        return _provider
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("all-MiniLM-L6-v2")
        _provider = SentenceTransformerProvider(model)
    except Exception as exc:  # not installed, no model cache, download failed…
        logger.warning(
            "sentence-transformers unavailable (%s); using DEV hashing fallback",
            exc,
        )
        _provider = HashingEmbeddingProvider()
    logger.info(
        "Embedding provider ACTIVE: %s (dim=%d)", _provider.name, _provider.dim
    )
    return _provider
