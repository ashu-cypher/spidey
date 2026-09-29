"""Phase 7 — tool-call observability + workflow-step ordering columns.

* tool_calls: add duration_ms, error (user-safe only), attempt (retry
  counter, 1-based), provider (settings.spidey_provider value at call time).
  Existing rows are backfilled with attempt=1.
* workflow_steps: add seq (original position in the run's step list) so
  DB-backed reads reconstruct steps in order. Existing rows backfill to 0.
"""

revision = "d7f2a9c41b05"
down_revision = "c9f4a2b8e1d7"

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
        "tool_calls",
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "tool_calls",
        sa.Column("provider", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "workflow_steps",
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("workflow_steps", "seq")
    op.drop_column("tool_calls", "provider")
    op.drop_column("tool_calls", "attempt")
    op.drop_column("tool_calls", "error")
    op.drop_column("tool_calls", "duration_ms")
