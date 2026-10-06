"""Zufaellige Fachfarben fuer bestehende Faecher.

Bisher bekam jedes Fach dasselbe Blau (``#3B82F6``), auch jedes importierte.
Neue Faecher waehlen ihre Farbe jetzt zufaellig (``random_subject_color``);
diese Revision faerbt die alten, noch blauen Faecher einmalig um. Eine von
Hand gesetzte andere Farbe bleibt unangetastet. Kein Schemawechsel.
"""

import random

import sqlalchemy as sa
from alembic import op

revision = "20261006_0013"
down_revision = "20261004_0012"
branch_labels = None
depends_on = None

# Bewusst kopiert statt importiert: eine Migration darf sich nicht mit dem Modell aendern.
SUBJECT_COLORS = (
    "#E11D48", "#F97316", "#F59E0B", "#84CC16", "#22C55E", "#14B8A6",
    "#06B6D4", "#0EA5E9", "#3B82F6", "#6366F1", "#8B5CF6", "#A855F7",
    "#D946EF", "#EC4899", "#78716C", "#0F766E",
)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, user_id FROM subjects WHERE upper(color) = '#3B82F6' ORDER BY user_id, id")
    ).all()
    # Je Nutzerin erst alle Farben einmal verteilen, dann von vorn - so sehen die
    # ersten sechzehn Faecher garantiert verschieden aus.
    pools: dict[int, list[str]] = {}
    for subject_id, user_id in rows:
        pool = pools.setdefault(user_id, [])
        if not pool:
            pool.extend(random.sample(SUBJECT_COLORS, len(SUBJECT_COLORS)))
        bind.execute(sa.text("UPDATE subjects SET color = :color WHERE id = :id"), {"color": pool.pop(), "id": subject_id})


def downgrade() -> None:
    # Die alten Farben waren alle gleich; zurueck ginge nur pauschal auf Blau.
    pass
