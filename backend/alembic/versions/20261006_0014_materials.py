"""Unterrichtsmaterial verstehen: Art, Thema, Datum und lokale Einbettungen.

Rein additiv. ``documents`` bekommt, was die App beim Einlesen erkennt
(Aufschrieb, Arbeitsblatt, Loesung; Thema; Datum; woher der Text stammt), und
``chunk_embeddings`` haelt die Vektoren des lokalen Einbettungsmodells.

Eigene Tabelle statt ``document_chunks.embedding``: jene Spalte ist auf
Postgres fest 1536 breit (Online-Provider) und auf SQLite gar kein Vektor. Das
lokale Modell hat eine andere Breite und laeuft vor allem auf SQLite (Desktop);
die Vektoren liegen hier als float32-Bytes, die Suche rechnet in Python.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261006_0014"
down_revision = "20261006_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("material_kind", sa.String(length=24), nullable=True))
        batch.add_column(sa.Column("material_topic", sa.String(length=240), nullable=True))
        batch.add_column(sa.Column("material_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("text_source", sa.String(length=24), nullable=True))
    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("dims", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(["chunk_id"], ["document_chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chunk_id"),
    )
    op.create_index("ix_chunk_embeddings_user_model", "chunk_embeddings", ["user_id", "model_id"])


def downgrade() -> None:
    op.drop_index("ix_chunk_embeddings_user_model", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("text_source")
        batch.drop_column("material_date")
        batch.drop_column("material_topic")
        batch.drop_column("material_kind")
