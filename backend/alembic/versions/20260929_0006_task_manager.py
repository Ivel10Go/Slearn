import sqlalchemy as sa
from alembic import op

revision = "20260929_0006"
down_revision = "20260925_0005"
branch_labels = None
depends_on = None

# Reihenfolge ist wichtig: `study_plan_tasks.series_id` zeigt auf
# `study_task_series`, das es vorher noch nicht gab.


def upgrade() -> None:
    # Vorlage fuer wiederkehrende Aufgaben. Die Tabelle enthaelt die
    # kommende Occurrence in `next_due_on`; NULL heisst "zu Ende".
    op.create_table(
        "study_task_series",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("study_plan_id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("recurrence", sa.String(length=16), nullable=False, server_default="weekly"),
        sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("starts_on", sa.DateTime(timezone=True), nullable=True),
        sa.Column("until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_due_on", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="0"),
        # Heisst nicht `limit`: das Wort ist ein reserviertes SQL-Schlüsselwort
        # und SQLite lehnt die Spalte im CREATE TABLE ab.
        sa.Column("occurrence_limit", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
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
        sa.CheckConstraint(
            "duration_minutes > 0", name="study_task_series_positive_duration"
        ),
        sa.CheckConstraint("priority >= 0", name="study_task_series_nonnegative_priority"),
        sa.CheckConstraint("occurrences >= 0", name="study_task_series_nonnegative_occurrences"),
        sa.CheckConstraint(
            "occurrence_limit IS NULL OR occurrence_limit > 0",
            name="study_task_series_positive_limit",
        ),        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["study_plan_id"], ["study_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_study_task_series_user_status",
        "study_task_series",
        ["user_id", "status"],
    )

    # Prioritaet, Faelligkeit und die Verweise auf Serie und Klausur.
    #
    # `batch_alter_table` ist auf SQLite zwingend, weil der Dialekt kein
    # `ALTER TABLE ... ADD CONSTRAINT` kennt. Auf Postgres laeuft derselbe
    # Block als normales ALTER.
    with op.batch_alter_table("study_plan_tasks") as batch:
        batch.add_column(sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("due_date", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("exam_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("series_id", sa.Integer(), nullable=True))
        batch.create_check_constraint("study_plan_tasks_nonnegative_priority", "priority >= 0")
        batch.create_foreign_key(
            "fk_study_plan_tasks_exam_id_exams", "exams", ["exam_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_foreign_key(
            "fk_study_plan_tasks_series_id_study_task_series",
            "study_task_series",
            ["series_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_study_plan_tasks_user_priority", "study_plan_tasks", ["user_id", "priority"])
    op.create_index("ix_study_plan_tasks_user_due", "study_plan_tasks", ["user_id", "due_date"])

    # `task_id` wartet auf `depends_on_task_id`. Beide Seiten loeschen ihre
    # Zeile mit, wenn die Aufgabe verschwindet.
    op.create_table(
        "study_task_dependencies",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("depends_on_task_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "task_id <> depends_on_task_id", name="study_task_dependencies_no_self"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["study_plan_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["depends_on_task_id"], ["study_plan_tasks.id"], ondelete="CASCADE"
        ),
        # Nur das Paar ist Schluessel: `user_id` ist eine normale Spalte fuer
        # die Nutzerisolation, sonst laesst sich eine Zeile nicht ueber ihr
        # Paar finden.
        sa.PrimaryKeyConstraint("task_id", "depends_on_task_id"),
    )
    op.create_index(
        "ix_study_task_dependencies_user_task",
        "study_task_dependencies",
        ["user_id", "task_id"],
    )
    op.create_index(
        "ix_study_task_dependencies_user_depends_on",
        "study_task_dependencies",
        ["user_id", "depends_on_task_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_study_task_dependencies_user_depends_on", table_name="study_task_dependencies"
    )
    op.drop_index("ix_study_task_dependencies_user_task", table_name="study_task_dependencies")
    op.drop_table("study_task_dependencies")

    op.drop_index("ix_study_plan_tasks_user_due", table_name="study_plan_tasks")
    op.drop_index("ix_study_plan_tasks_user_priority", table_name="study_plan_tasks")
    with op.batch_alter_table("study_plan_tasks") as batch:
        batch.drop_constraint(
            "fk_study_plan_tasks_series_id_study_task_series", type_="foreignkey"
        )
        batch.drop_constraint("fk_study_plan_tasks_exam_id_exams", type_="foreignkey")
        batch.drop_constraint("study_plan_tasks_nonnegative_priority", type_="check")
        batch.drop_column("series_id")
        batch.drop_column("exam_id")
        batch.drop_column("due_date")
        batch.drop_column("priority")

    op.drop_index("ix_study_task_series_user_status", table_name="study_task_series")
    op.drop_table("study_task_series")
