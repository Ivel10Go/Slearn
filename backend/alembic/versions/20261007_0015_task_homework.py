"""Lernaufgaben kennen ihre Hausaufgabe.

Der automatische Lernplan (``app/services/study_planner.py``) legt fuer offene
Hausaufgaben eine Lernaufgabe an. Ohne Verweis waere ein zweiter Lauf eine
zweite Aufgabe fuer dieselbe Hausaufgabe - der Titel taugt nicht als Schluessel.
``SET NULL`` beim Loeschen der Hausaufgabe: die Lernaufgabe bleibt mit ihrer
Lernzeit stehen.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261007_0015"
down_revision = "20261006_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `batch_alter_table`, weil SQLite kein ADD CONSTRAINT kennt.
    with op.batch_alter_table("study_plan_tasks") as batch:
        batch.add_column(sa.Column("homework_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_study_plan_tasks_homework_id_homework", "homework", ["homework_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("study_plan_tasks") as batch:
        batch.drop_constraint("fk_study_plan_tasks_homework_id_homework", type_="foreignkey")
        batch.drop_column("homework_id")
