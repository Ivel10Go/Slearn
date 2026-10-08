"""Der Willkommenssatz merkt sich seine Sprache.

Die Oberflaeche gibt es auf Deutsch und Englisch. Der Satz bleibt ein Eintrag je
Nutzer und Tag; passt die gespeicherte Sprache nicht zur angefragten, wird er
neu geschrieben und ersetzt. Ein Sprachwechsel kostet damit genau einen
Modellaufruf, und die Eindeutigkeit (Nutzer, Tag) bleibt unberuehrt.
Bestehende Zeilen sind deutsch - deshalb der Default.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008_0016"
down_revision = "20261007_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("greeting_cache") as batch:
        batch.add_column(sa.Column("language", sa.String(length=8), nullable=False, server_default="de"))


def downgrade() -> None:
    with op.batch_alter_table("greeting_cache") as batch:
        batch.drop_column("language")
