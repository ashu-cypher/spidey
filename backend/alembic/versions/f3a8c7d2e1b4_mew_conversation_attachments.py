"""MEW upgrade — conversation_attachments table (files as chat context).

* conversation_attachments: rows written by POST /api/chat/attach
  (id, conversation_id [indexed], filename, kind, extracted_text,
  created_at).
"""

revision = "f3a8c7d2e1b4"
down_revision = "e8f1a2b3c4d5"

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = revision
down_revision: Union[str, None] = down_revision
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_attachments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_conversation_attachments_conversation_id"),
        "conversation_attachments",
        ["conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_conversation_attachments_conversation_id"),
        table_name="conversation_attachments",
    )
    op.drop_table("conversation_attachments")
