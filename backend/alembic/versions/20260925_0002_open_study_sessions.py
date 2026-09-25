import sqlalchemy as sa
from alembic import op

revision = "20260925_0002"
down_revision = "20260925_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_study_sessions_user_task_open",
        "study_sessions",
        ["user_id", "task_id"],
        unique=True,
        sqlite_where=sa.text("completed_at IS NULL"),
        postgresql_where=sa.text("completed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_study_sessions_user_task_open", table_name="study_sessions")
