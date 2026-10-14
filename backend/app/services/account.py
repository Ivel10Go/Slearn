"""Datenauskunft und Kontoloeschung (DSGVO Art. 15/20 und Art. 17).

Beide Wege laufen **generisch ueber alle Tabellen** mit einer Spalte
``user_id`` - und das sind alle ausser ``users`` selbst. Eine Liste von Hand
waere nach der naechsten Migration unvollstaendig, und eine vergessene Tabelle
hiesse: die Auskunft verschweigt Daten, oder die Loeschung laesst welche
zurueck. ``test_account.py`` prueft deshalb, dass jede Tabelle erfasst ist.

Geloescht wird **ausdruecklich je Tabelle in umgekehrter Abhaengigkeits-
reihenfolge**, nicht ueber ``ON DELETE CASCADE``: SQLite setzt Fremdschluessel
nur mit ``PRAGMA foreign_keys=ON`` durch, und eine Verbindung ohne diese
Einstellung liesse sonst alle Kinderzeilen stehen.

Was die Auskunft weglaesst, steht in ``REDACTED`` - Geheimnisse, die niemandem
nuetzen und in einer weitergereichten Datei schaden (Passwort-Hash,
verschluesselte Zugangsdaten, Push-Schluessel, das Kalender-Abo-Token), und
abgeleitete Zahlenreihen (Embeddings), die sich aus den Texten neu rechnen.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import shutil
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from sqlalchemy import Table, delete, select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.db.base import Base
from app.db.entities import Document, User

logger = logging.getLogger("school_assistant.account")

#: Die Bestaetigung, die die Loeschung verlangt. Ein versehentlicher Klick
#: tippt das nicht.
DELETE_CONFIRMATION: Final = "LÖSCHEN"

#: Tabelle -> Spalten, deren Inhalt die Auskunft nicht enthaelt.
REDACTED: Final[dict[str, frozenset[str]]] = {
    "users": frozenset({"password_hash"}),
    "sync_sources": frozenset({"credentials"}),
    "push_subscriptions": frozenset({"endpoint", "p256dh", "auth"}),
    "document_chunks": frozenset({"embedding"}),
    "chunk_embeddings": frozenset({"vector"}),
}
#: Schluessel in ``settings.preferences``, die ein Geheimnis sind.
REDACTED_PREFERENCES: Final = frozenset({"calendar_feed_token"})
REDACTED_MARK: Final = "[nicht enthalten]"

EXPORT_FORMAT: Final = "lernraum-export-1"

README_TEXT: Final = """Slearn - Datenauskunft
========================

daten.json enthaelt alle Eintraege deines Kontos, je Tabelle eine Liste.
Zeitangaben sind UTC (ISO 8601).

Nicht enthalten sind Geheimnisse und abgeleitete Werte:
- Passwort-Hash, verschluesselte Zugangsdaten (WebUntis, KI-Dienst, Kalender),
  Push-Schluessel des Browsers und das Kalender-Abo-Token,
- Embeddings (Zahlenreihen fuer die Suche, aus den Texten berechnet).

Der Ordner dokumente/ enthaelt die Dateien, die du in Slearn hochgeladen
hast. Dateien aus einem verbundenen Ordner liegen weiter dort und sind hier
nicht doppelt enthalten.

Trainingsrunden und persoenliche Adapter (falls vorhanden) sind Ableitungen
deiner Gespraeche und liegen nicht in dieser Datei; sie werden beim Loeschen
des Kontos mit entfernt.
"""


@dataclass(frozen=True)
class DeletionResult:
    rows: int
    files: int


def user_tables() -> list[Table]:
    """Alle Tabellen mit ``user_id``, Kinder vor Eltern (Loeschreihenfolge)."""
    return [table for table in reversed(Base.metadata.sorted_tables) if "user_id" in table.columns]


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    return value


def _row(table: Table, row: Any) -> dict[str, Any]:
    hidden = REDACTED.get(table.name, frozenset())
    result: dict[str, Any] = {}
    for column in table.columns:
        value = row._mapping[column]
        if column.name in hidden and value is not None:
            result[column.name] = REDACTED_MARK
        elif table.name == "settings" and column.name == "preferences" and isinstance(value, dict):
            result[column.name] = {
                key: (REDACTED_MARK if key in REDACTED_PREFERENCES else _plain(item)) for key, item in value.items()
            }
        else:
            result[column.name] = _plain(value)
    return result


def collect_user_data(db: Session, user: User) -> dict[str, Any]:
    """Alle Zeilen des Nutzers als JSON-taugliches Woerterbuch."""
    users_table = Base.metadata.tables["users"]
    user_row = db.execute(select(users_table).where(users_table.c.id == user.id)).one()
    tables: dict[str, list[dict[str, Any]]] = {"users": [_row(users_table, user_row)]}
    for table in sorted(user_tables(), key=lambda item: item.name):
        order = [column for column in table.primary_key.columns]
        rows = db.execute(select(table).where(table.c.user_id == user.id).order_by(*order)).all()
        tables[table.name] = [_row(table, row) for row in rows]
    return {
        "format": EXPORT_FORMAT,
        "exported_at": utc_now().isoformat(),
        "user_id": user.id,
        "tables": tables,
    }


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def managed_documents(db: Session, user_id: int, storage: Path) -> list[tuple[Document, Path]]:
    """Dokumente, deren Datei Slearn selbst verwaltet (Ablage unter ``DOCUMENT_STORAGE_PATH``).

    Dateien eines verbundenen Ordners gehoeren dem Nutzer, nicht der App - sie
    werden weder exportiert (sie liegen ja schon dort) noch geloescht.
    """
    documents = db.scalars(select(Document).where(Document.user_id == user_id).order_by(Document.id)).all()
    found: list[tuple[Document, Path]] = []
    for document in documents:
        path = Path(document.storage_path)
        if document.source != "documents" and _inside(path, storage) and path.is_file():
            found.append((document, path))
    return found


def _safe_name(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._- " else "_" for char in name).strip()
    return cleaned or "datei"


def build_export(db: Session, user: User, storage: Path) -> bytes:
    """Die Auskunft als ZIP: ``daten.json``, ``LIESMICH.txt`` und ``dokumente/``."""
    data = collect_user_data(db, user)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("LIESMICH.txt", README_TEXT)
        archive.writestr("daten.json", json.dumps(data, ensure_ascii=False, indent=2))
        for document, path in managed_documents(db, user.id, storage):
            try:
                archive.write(path, f"dokumente/{document.id}-{_safe_name(document.filename)}")
            except OSError as exc:
                logger.warning("Dokument %s fehlt im Export: %s", document.id, exc)
    return buffer.getvalue()


def delete_user_data(db: Session, user: User, storage: Path) -> DeletionResult:
    """Loescht das Konto mit allen Zeilen und den selbst verwalteten Dateien.

    Erst die Datenbank, dann die Dateien: scheitert das Commit, ist nichts
    verloren. Eine Datei, die danach nicht geloescht werden kann, ist ohne
    Zeile nicht mehr erreichbar und wird nur protokolliert.
    """
    user_id = user.id
    paths = [path for _document, path in managed_documents(db, user_id, storage)]
    rows = 0
    for table in user_tables():
        result = db.execute(delete(table).where(table.c.user_id == user_id))
        rows += int(getattr(result, "rowcount", 0) or 0)
    users_table = Base.metadata.tables["users"]
    db.execute(delete(users_table).where(users_table.c.id == user_id))
    db.commit()
    db.expunge_all()
    files = 0
    for path in paths:
        try:
            path.unlink()
            files += 1
        except OSError as exc:
            logger.warning("Datei %s konnte nicht geloescht werden: %s", path.name, exc)
    return DeletionResult(rows=rows + 1, files=files)


def remove_tree(path: Path) -> None:
    """Entfernt einen Ordner, wenn es ihn gibt - Fehler werden nur protokolliert."""
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
