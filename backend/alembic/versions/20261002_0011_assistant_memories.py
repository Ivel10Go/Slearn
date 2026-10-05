"""Gedaechtnis des Assistenten.

Rein additiv: eine neue Tabelle, keine bestehende Spalte wird angefasst.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261002_0011"
down_revision = "20260930_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_memories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.String(length=400), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="assistant"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("source IN ('assistant', 'manual')", name="assistant_memories_known_source"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_memories_user", "assistant_memories", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_assistant_memories_user", table_name="assistant_memories")
    op.drop_table("assistant_memories")
