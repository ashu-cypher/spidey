"""Phase 4 — resume versioning columns.

Revision ID: c9f4a2b8e1d7
Revises: a4f7c2d91e5b
Create Date: 2026-09-29

* resume_versions: add version_number (per-user monotonic counter),
  source_filename, created_from ("upload" | "improvement" | "job_match" |
  source version id for restores). Existing rows are backfilled with a
  per-user row number ordered by created_at.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9f4a2b8e1d7"
down_revision: Union[str, None] = "a4f7c2d91e5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "resume_versions",
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "resume_versions",
        sa.Column("source_filename", sa.String(512), nullable=False, server_default=""),
    )
    op.add_column(
        "resume_versions",
        sa.Column("created_from", sa.String(64), nullable=True),
    )
    # Backfill: per-user row number ordered by creation time.
    op.execute(
        sa.text(
            "WITH ranked AS ("
            " SELECT id, ROW_NUMBER() OVER"
            " (PARTITION BY user_id ORDER BY created_at, id) AS rn"
            " FROM resume_versions"
            ") UPDATE resume_versions SET version_number = ranked.rn"
            " FROM ranked WHERE resume_versions.id = ranked.id"
        )
    )


def downgrade() -> None:
    op.drop_column("resume_versions", "created_from")
    op.drop_column("resume_versions", "source_filename")
    op.drop_column("resume_versions", "version_number")
