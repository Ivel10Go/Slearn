import sqlalchemy as sa
from alembic import op

revision = "20260925_0005"
down_revision = "20260925_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Haelt den Willkommenssatz je Nutzer und Tag, damit ein Modellaufruf nicht
    # bei jedem Oeffnen der Dashboard-Seite erfolgt.
    op.create_table(
        "greeting_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("sentence", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="ai"),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("generated_on", sa.Date(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "generated_on", name="uq_greeting_cache_user_day"),
    )


def downgrade() -> None:
    op.drop_table("greeting_cache")
