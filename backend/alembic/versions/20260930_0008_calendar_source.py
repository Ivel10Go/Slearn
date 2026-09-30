"""Zustand der Termine gegenüber ihrem Kalender.

`calendar_events` hat das Quellen-Attribut **schon**: `sync_source_id`,
`source` und `external_id` mit der eindeutigen Kombination
`(user_id, source, external_id)`. Diese Revision ist deshalb *nicht* die aus
der Aufgabenliste ("Quellen-Attribut an `CalendarEvent`") - die ist seit dem
Basisschema erledigt.

Was fehlt, ist die zweite Hälfte eines bidirektionalen Abgleichs. Um zu
entscheiden, wessen Änderung gilt, müssen zwei Dinge bekannt sein:

* **Hat die Gegenseite sich seit dem letzten Lauf geändert?** Dafür hält die
  Zeile den Zustand, den sie zuletzt gesehen hat. `remote_etag` ist die
  ETag/Revision der Gegenseite, `remote_modified_at` deren eigener
  Änderungszeitpunkt - beide gehören zu *ihrer* Fassung, nicht zu `updated_at`,
  das nur lokale Änderungen abbildet.
* **Hat die Lernende in der App etwas geändert?** Dafür ist `sync_state` da:
  `synced` (beide Seiten sind gleich), `edited` (lokal geändert, der Gegenseite
  nachzuschieben) und `deleted` (lokal gelöscht, ein Grabstein). Ohne diese
  Spalte müsste ein Abruf entscheiden, ob er eine fremde Änderung zurückschreiben
  darf, und das Raten wäre genau das, was lokale Änderungen zerstört.

`sync_state` ist **kein Boolean**. Zwei Flags ("geändert" und "gelöscht")
hätten einen vierten, widersprüchlichen Zustand erlaubt, den niemand
behandeln müsste. Drei benannte Zustände mit einer Randbedingung sind
erschöpfend.

Die Randbedingung: `sync_state = 'deleted'` ist ein Grabstein und wird aus
jeder Liste ausgefiltert. Der Termin ist weg, die Zeile bleibt aber, weil das
`DELETE` in der Gegenseite noch geschehen muss - und dafür braucht es das
`remote_etag`, sonst würde der Löschauftrag genau den falschen Termin treffen.

`remote_etag` und `remote_modified_at` heißen nicht `etag`/`modified_at`, weil
der Präfix sichtbar machen soll, dass die Werte die Gegenseite beschreiben.

Reihenfolge: Tabelle und beide Indizes existieren seit `0001`, deshalb hängt
nur eine Revision daran.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260930_0008"
down_revision = "20260930_0007"
branch_labels = None
depends_on = None

_SYNC_STATES = "sync_state IN ('synced', 'edited', 'deleted')"


def upgrade() -> None:
    # `batch_alter_table` ist hier Pflicht und nicht Geschmack: SQLite kennt kein
    # `ALTER TABLE ... ADD CONSTRAINT`. Alembic baut die Einschränkung dort
    # otherwise nicht, sondern braucht den Batch-Weg, der die Tabelle
    # zwischendurch nachbaut. Auf Postgres geht der Batch-Weg als normales
    # ALTER durch - dieselbe Zeile, beide Dialekte.
    with op.batch_alter_table("calendar_events") as batch:
        batch.add_column(sa.Column("sync_state", sa.String(length=16), nullable=False, server_default="synced"))
        batch.add_column(sa.Column("remote_etag", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("remote_modified_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint("calendar_events_known_sync_state", _SYNC_STATES)
    op.create_index("ix_calendar_events_user_sync_state", "calendar_events", ["user_id", "sync_state"])


def downgrade() -> None:
    op.drop_index("ix_calendar_events_user_sync_state", table_name="calendar_events")
    with op.batch_alter_table("calendar_events") as batch:
        batch.drop_constraint("calendar_events_known_sync_state", type_="check")
        batch.drop_column("remote_modified_at")
        batch.drop_column("remote_etag")
        batch.drop_column("sync_state")
