"""Klassenarbeit als Arbeitsbereich: ausgewaehltes Material und im Studio Erzeugtes.

``exam_documents`` haelt die Auswahl (Primaerschluessel nur das Paar, sonst
findet ``db.get`` nichts). ``exam_creations`` haelt Zusammenfassungen,
Karteikarten usw., damit sie nicht bei jedem Oeffnen neu Modellzeit kosten.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261119_0017"
down_revision = "20261008_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exam_documents",
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("exams.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_table(
        "exam_creations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("items", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_exam_creations_user_exam", "exam_creations", ["user_id", "exam_id"])


def downgrade() -> None:
    op.drop_index("ix_exam_creations_user_exam", table_name="exam_creations")
    op.drop_table("exam_creations")
    op.drop_table("exam_documents")
