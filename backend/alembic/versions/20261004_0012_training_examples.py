"""Trainingsbeispiele fuer das spaetere Feintuning je Nutzer.

Rein additiv: eine neue Tabelle und eine nullbare Spalte an
``assistant_memories``. Gesammelt wird nur mit Freigabe
(``settings.preferences["train_on_conversations"]``, Standard aus).
"""

import sqlalchemy as sa
from alembic import op

revision = "20261004_0012"
down_revision = "20261002_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_examples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("completion", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("server_adjusted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("applied_actions", sa.JSON(), nullable=False),
        sa.Column("rejected_actions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("memory_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("user_rating", sa.String(length=8), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            "user_rating IS NULL OR user_rating IN ('good', 'bad')", name="training_examples_known_rating"
        ),
        sa.CheckConstraint("rejected_actions >= 0", name="training_examples_rejected_non_negative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_training_examples_user", "training_examples", ["user_id", "created_at"])
    # batch_alter_table, weil SQLite (Desktop, Tests) keinen FK per ALTER TABLE kennt.
    with op.batch_alter_table("assistant_memories") as batch:
        batch.add_column(sa.Column("training_example_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_assistant_memories_training_example",
            "training_examples",
            ["training_example_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("assistant_memories") as batch:
        batch.drop_constraint("fk_assistant_memories_training_example", type_="foreignkey")
        batch.drop_column("training_example_id")
    op.drop_index("ix_training_examples_user", table_name="training_examples")
    op.drop_table("training_examples")
