"""MEW Phase 2 — image bytes on conversation_attachments (spec section 18).

* conversation_attachments: add data_base64 (Text, nullable) — the
  base64-encoded bytes for kind='image' attachments (bounded at write
  time), so a vision-capable provider can actually receive the image.
  NULL for document/resume attachments.
"""

revision = "c3d2e5a8f1b4"
down_revision = "b2c1a4f7d9e0"

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = revision
down_revision: Union[str, None] = down_revision
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation_attachments",
        sa.Column("data_base64", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation_attachments", "data_base64")
