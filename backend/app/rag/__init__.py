"""Phase 3 — retrieval-augmented generation (RAG) package.

Submodules are imported directly (``app.rag.pipeline`` etc.); this package
``__init__`` stays import-light on purpose so ``app.models`` can safely import
``EMBEDDING_DIM`` from ``app.rag.embeddings`` without a circular import.
"""
