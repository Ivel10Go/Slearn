"""Kontext-Stufe je Material einer Klassenarbeit: ganzer Text oder nur Kurzfassung.

``full`` (Standard) schickt den Text, ``summary`` eine einmal erzeugte Kurzfassung
(gemerkt in ``documents.extra_data["notebook_summary"]``). Bei 12 000 Zeichen
Budget passt so viel mehr Material in den Prompt eines kleinen lokalen Modells.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261120_0018"
down_revision = "20261119_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("exam_documents") as batch:
        batch.add_column(sa.Column("mode", sa.String(16), nullable=False, server_default="full"))


def downgrade() -> None:
    with op.batch_alter_table("exam_documents") as batch:
        batch.drop_column("mode")
