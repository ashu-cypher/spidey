"""MEW Phase 2 — user_profile table (spec section 8).

* user_profile: one row per user (user_id PK), all fields user-supplied
  and nullable (name, education, college, projects JSON, skills JSON,
  goals, preferences JSON, updated_at). Unknown fields stay NULL — the
  agent reports them honestly as unknown, never invented.
"""

revision = "b2c1a4f7d9e0"
down_revision = "f3a8c7d2e1b4"

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
        "user_profile",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column("education", sa.Text(), nullable=True),
        sa.Column("college", sa.String(length=256), nullable=True),
        sa.Column("projects", sa.JSON(), nullable=True),
        sa.Column("skills", sa.JSON(), nullable=True),
        sa.Column("goals", sa.Text(), nullable=True),
        sa.Column("preferences", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_profile")
