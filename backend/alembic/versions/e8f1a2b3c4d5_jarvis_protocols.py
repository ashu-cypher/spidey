"""MISSION J.A.R.V.I.S. — protocol audit table + reminder notified flag.

* protocol_audits: audit trail written by POST /api/protocols/trigger
  (protocol_id, protocol_name, response_text, triggered_at).
* reminders: add notified (boolean, default false) so the event-bus poller
  (ReminderTool.list_due) claims each due reminder exactly once.
"""

revision = "e8f1a2b3c4d5"
down_revision = "d7f2a9c41b05"

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
        "protocol_audits",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("protocol_id", sa.String(length=64), nullable=False),
        sa.Column("protocol_name", sa.String(length=128), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column("triggered_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_protocol_audits_protocol_id"),
        "protocol_audits",
        ["protocol_id"],
        unique=False,
    )
    op.add_column(
        "reminders",
        sa.Column("notified", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("reminders", "notified")
    op.drop_index(
        op.f("ix_protocol_audits_protocol_id"), table_name="protocol_audits"
    )
    op.drop_table("protocol_audits")
