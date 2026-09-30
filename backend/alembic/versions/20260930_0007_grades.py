"""Noten als eigenes Modell.

`Exam` wird hier **nicht** erweitert. Eine Klausur ist ein Termin, eine Note ist
ein Ergebnis: eine abgegebene Arbeit hat kein Datum, an dem sie stattfindet, und
eine Klausur ohne Note ist keine Note. Beides in eine Tabelle zu pressen hiesse,
dass jede zweite Spalte NULL sein muss.

Reihenfolge: die Tabelle wird neu angelegt, also gibt es hier nichts, was von
einer bestehenden Reihenfolge abhaengt. `documents` existiert seit `0001`.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260930_0007"
down_revision = "20260929_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "grades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("document_id", sa.Integer(), nullable=True),
        # Heisst nicht `value`: das ist in mehreren SQL-Dialekten ein
        # Schluesselwort. `Float` reicht, weil die Skala hoechstens eine
        # Nachkommastelle hat.
        sa.Column("grade_value", sa.Float(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False, server_default="written_exam"),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="1"),
        # Ein Kalendertag, kein Zeitstempel: eine Note hat keinen Zeitpunkt.
        sa.Column("graded_on", sa.Date(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="manual"),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("weight > 0", name="grades_positive_weight"),
        sa.CheckConstraint("grade_value > 0 AND grade_value <= 6", name="grades_value_in_scale"),
        sa.CheckConstraint(
            "kind IN ('written_exam', 'test', 'oral')",
            name="grades_known_kind",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        # Macht den Import aus einem Dokument idempotent: derselbe Text ergibt
        # denselben Schluessel, ein zweiter Import legt nichts doppelt an. Von
        # Hand erfasste Noten haben `external_id = NULL`, und NULL-Werte sind in
        # SQL nicht gleich - mehrere davon sind also erlaubt.
        sa.UniqueConstraint("user_id", "source", "external_id", name="uq_grades_user_source_external"),
    )
    op.create_index("ix_grades_user_subject", "grades", ["user_id", "subject_id"])
    op.create_index("ix_grades_user_date", "grades", ["user_id", "graded_on"])


def downgrade() -> None:
    op.drop_index("ix_grades_user_date", table_name="grades")
    op.drop_index("ix_grades_user_subject", table_name="grades")
    op.drop_table("grades")
