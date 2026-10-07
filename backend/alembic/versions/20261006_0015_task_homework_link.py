"""Lernaufgaben koennen an einer Hausaufgabe haengen.

Der Wochenplaner legt fuer eine offene Hausaufgabe einen Lernblock an. Ohne
diese Verbindung wuerde jeder neue Planungslauf denselben Block noch einmal
anlegen, und das Erledigen des Blocks wuesste nicht, welche Hausaufgabe
damit fertig ist. Rein additiv: eine nullbare Spalte.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261006_0015"
down_revision = "20261006_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch_alter_table, weil SQLite (Desktop, Tests) keinen FK per ALTER TABLE kennt.
    with op.batch_alter_table("study_plan_tasks") as batch:
        batch.add_column(sa.Column("homework_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_study_plan_tasks_homework",
            "homework",
            ["homework_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("study_plan_tasks") as batch:
        batch.drop_constraint("fk_study_plan_tasks_homework", type_="foreignkey")
        batch.drop_column("homework_id")
