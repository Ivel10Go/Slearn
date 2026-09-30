"""Erinnerungen und Web-Push-Abonnements.

Rein additiv: zwei neue Tabellen, keine Aenderung an einer bestehenden Spalte.
`settings.notifications_enabled` aus `0001` bleibt, wo sie ist - sie wird von
dieser Welle aus nur noch gelesen und geschrieben, nicht mehr umgebaut.

Zur Reihenfolge: `notifications` und `push_subscriptions` zeigen beide nur auf
`users`, das es seit `0001` gibt. Eine Umbenennung von Revisionen waere nicht
moeglich, und sie waere auch nicht noetig - es gibt hier nichts, was von einer
bestehenden Reihenfolge abhaengt.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260930_0008"
down_revision = "20260930_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Die Erinnerung als Zeile. `delivered_at` bleibt NULL, solange kein
    # Push-Dienst die Nachricht angenommen hat - und genau solange holt die
    # offene Seite sie ueber den lokalen Weg ab.
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("body", sa.String(length=400), nullable=False),
        sa.Column("url", sa.String(length=240), nullable=False),
        # Kein Fremdschluessel auf Aufgaben oder Klausuren: die Zeile soll
        # eine geloeschte Quelle ueberleben statt still zu verschwinden.
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=120), nullable=False),
        sa.Column("push_status", sa.String(length=16), nullable=False, server_default="skipped"),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "kind IN ('study_task_due', 'exam_due')",
            name="notifications_known_kind",
        ),
        sa.CheckConstraint(
            "push_status IN ('skipped', 'sent', 'failed')",
            name="notifications_known_push_status",
        ),
        # Das Rueckgrat der Idempotenz: ein Lauf, ein lokaler Tag, eine Zeile.
        sa.UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe_key"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"])

    # Ein Abo pro Endgeraet. Der Endpunkt ist die Identitaet des Geraets,
    # deshalb ist er Teil des Schluessels - ein zweiter Anmeldeversuch ersetzt
    # die Zeile, statt eine zweite anzulegen.
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("user_agent", sa.String(length=320), nullable=True),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("user_id", "endpoint", name="uq_push_subscriptions_user_endpoint"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_push_subscriptions_user_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_table("notifications")
