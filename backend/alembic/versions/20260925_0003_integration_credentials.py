import sqlalchemy as sa
from alembic import op

revision = "20260925_0003"
down_revision = "20260925_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sync_sources", sa.Column("credentials", sa.Text(), nullable=True))
    op.add_column(
        "sync_sources",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="configured"),
    )
    op.add_column("sync_sources", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("sync_sources", sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_sync_sources_user_type", "sync_sources", ["user_id", "source_type"])


def downgrade() -> None:
    op.drop_index("ix_sync_sources_user_type", table_name="sync_sources")
    op.drop_column("sync_sources", "last_tested_at")
    op.drop_column("sync_sources", "last_error")
    op.drop_column("sync_sources", "status")
    op.drop_column("sync_sources", "credentials")
