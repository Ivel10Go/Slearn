"""Punktekonto und Auszeichnungen.

Rein additiv: zwei neue Tabellen, keine bestehende Spalte wird angefasst. Damit
gibt es hier auch nichts, wofuer `batch_alter_table` noetig waere - das Problem,
dass SQLite kein `ALTER TABLE ... ADD CONSTRAINT` kennt, entsteht erst bei einer
Aenderung an einer existierenden Tabelle. `op.create_table` verstehen beide
Dialekte gleich, und `op.drop_table` im `downgrade` erst recht.

Zur Reihenfolge: `down_revision = "20260930_0009"` ist der Notifications-Head.
Diese Welle und die Noten-Welle sind unabhaengig voneinander entstanden und
hatten beide eine eigene Nummer im Rennen - ein gleichzeitiger Head waere ein
`alembic upgrade head`, das mit "multiple heads" abbricht.

Die beiden eindeutigen Constraints sind das Rueckgrat der Idempotenz:

* `uq_reward_entries_user_kind_event` macht aus ``(Nutzer, Art, Ereignis)`` ein
  Ereignis, das genau einmal vergueten kann. Ein zweiter Aufruf von
  ``POST /api/v1/study/tasks/{id}/complete`` stoesst darauf, statt ein zweites
  Mal zu gutschreiben.
* `uq_reward_badges_user_code_scope` haelt die Auszeichnung je Bereich
  einmalig. Der Bereich ist eine **eigene Spalte** und kein NULL-Fremdschluessel,
  weil zwei NULL in SQL nicht gleich sind - sonst duerfte es beliebig viele
  globale Auszeichnungen mit demselben Code geben.

Beide Namen haben das Praefix `uq_`, weil der Name in Postgres und in SQLite in
die Fehlermeldung des Refactors wandert und dort ohne Hinweis auf seine Rolle
nichts nuetzt.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260930_0010"
down_revision = "20260930_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Das Punktekonto. Der Stand des Kontos ist die Summe ueber `points` - es
    # gibt bewusst keine Spalte, in der ein laufender Stand stuende. Ein solcher
    # Zaehler muesste bei jedem Buchen nachgefuehrt werden, und genau dann
    # entstehen die doppelten Gutschriften, die diese Tabelle verhindern soll.
    op.create_table(
        "reward_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        # Der Ereignisschluessel traegt das Ereignis, nicht die Uhrzeit:
        # `task:41` bzw. `subject-day:7:2026-09-30`.
        sa.Column("event_key", sa.String(length=120), nullable=False),
        # Null ist zulaessig und bedeutet: heute war das Tageskonto schon leer.
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("detail", sa.String(length=240), nullable=False),
        # Ein Kalendertag in der Zeitzone des Nutzers, kein UTC-Zeitstempel.
        sa.Column("earned_on", sa.Date(), nullable=False),
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
        sa.CheckConstraint("points >= 0", name="reward_entries_nonnegative_points"),
        sa.CheckConstraint("minutes >= 0", name="reward_entries_nonnegative_minutes"),
        sa.CheckConstraint(
            "kind IN ('task_completed', 'subject_day')",
            name="reward_entries_known_kind",
        ),
        sa.UniqueConstraint("user_id", "kind", "event_key", name="uq_reward_entries_user_kind_event"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        # Fach und Thema duerfen verschwinden, ohne die Buchung zu loeschen:
        # "an diesem Tag gelernt" bleibt auch dann wahr.
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Die Tagesobergrenze wird je Buchung gelesen, deshalb steht sie zuerst.
    op.create_index("ix_reward_entries_user_day", "reward_entries", ["user_id", "earned_on"])
    op.create_index("ix_reward_entries_user_subject", "reward_entries", ["user_id", "subject_id"])

    # Die Auszeichnungen. `progress` und `target` tragen die Bedingung in Zahlen,
    # damit die Oberflaeche keinen eigenen Zaehler fuehren muss.
    op.create_table(
        "reward_badges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=48), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False, server_default="global"),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("target", sa.Integer(), nullable=False),
        # NULL heisst: der Fortschritt reicht noch nicht.
        sa.Column("awarded_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("progress >= 0", name="reward_badges_nonnegative_progress"),
        sa.CheckConstraint("target >= 1", name="reward_badges_positive_target"),
        sa.UniqueConstraint("user_id", "code", "scope", name="uq_reward_badges_user_code_scope"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reward_badges_user_code", "reward_badges", ["user_id", "code"])


def downgrade() -> None:
    op.drop_index("ix_reward_badges_user_code", table_name="reward_badges")
    op.drop_table("reward_badges")
    op.drop_index("ix_reward_entries_user_subject", table_name="reward_entries")
    op.drop_index("ix_reward_entries_user_day", table_name="reward_entries")
    op.drop_table("reward_entries")
