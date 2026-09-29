"""Phase 3 — RAG knowledge base columns + 384-dim embeddings.

Revision ID: a4f7c2d91e5b
Revises: 031bbd97dc21
Create Date: 2026-09-29

* documents: add filename, content_type, status, chunk_count
* document_chunks: add source, created_at; embedding vector(1536) -> vector(384)
  (matches EMBEDDING_DIM used by all Phase 3 embedding providers)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy


# revision identifiers, used by Alembic.
revision: str = "a4f7c2d91e5b"
down_revision: Union[str, None] = "031bbd97dc21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # documents: Phase 3 metadata
    op.add_column("documents", sa.Column("filename", sa.String(512), nullable=False, server_default=""))
    op.add_column("documents", sa.Column("content_type", sa.String(128), nullable=False, server_default=""))
    op.add_column("documents", sa.Column("status", sa.String(32), nullable=False, server_default="ready"))
    op.add_column("documents", sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"))
    # document_chunks: source + created_at metadata
    op.add_column("document_chunks", sa.Column("source", sa.String(512), nullable=False, server_default=""))
    op.add_column(
        "document_chunks",
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    # embedding: 1536 -> 384 dims (Phase 3 providers all emit 384-dim vectors)
    op.alter_column(
        "document_chunks",
        "embedding",
        existing_type=pgvector.sqlalchemy.Vector(1536),
        type_=pgvector.sqlalchemy.Vector(384),
        postgresql_using="embedding::vector(384)",
    )


def downgrade() -> None:
    op.alter_column(
        "document_chunks",
        "embedding",
        existing_type=pgvector.sqlalchemy.Vector(384),
        type_=pgvector.sqlalchemy.Vector(1536),
        postgresql_using="embedding::vector(1536)",
    )
    op.drop_column("document_chunks", "created_at")
    op.drop_column("document_chunks", "source")
    op.drop_column("documents", "chunk_count")
    op.drop_column("documents", "status")
    op.drop_column("documents", "content_type")
    op.drop_column("documents", "filename")
