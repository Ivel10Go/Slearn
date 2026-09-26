import sqlalchemy as sa
from alembic import op

revision = "20260925_0004"
down_revision = "20260925_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Kuerzel aus dem WebUntis-Fach, damit der Plan einzeilig bleibt statt
    # "Französisch" zu erzwingen. Fuer manuell angelegte Faechern bleibt es NULL.
    op.add_column("subjects", sa.Column("short_name", sa.String(length=20), nullable=True))
    op.add_column(
        "lessons",
        sa.Column("is_cancelled", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("lessons", "is_cancelled")
    op.drop_column("subjects", "short_name")
