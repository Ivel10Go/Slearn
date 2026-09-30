"""Liest den Backup-Block aus der Statusdatei des Workers.

Das Backend kennt den Worker nicht und spricht nicht mit ihm. Beide sehen
dieselbe Datei, weil sie am Named Volume ``worker-status`` hängen - das ist
bewusst der einzige Kopplungspunkt, und er braucht keinen Port, keinen
authentifizierten Aufruf und keine Datenbanktabelle.

Zwei Regeln prägen die Übersetzung:

* **Nichts wird erfunden.** Fehlt die Datei, ist das Volume nicht geteilt, oder
  kennt der Worker den Runner noch nicht, dann kommt ein deutscher Klartext
  zurück und ``backup`` bleibt ``null``. Es gibt keinen Platzhalter, der so
  aussieht wie ein Ergebnis.
* **Zeitstempel ohne Zeitzone sind ungültig.** ``app.schemas.common.UTCResponseModel``
  würde eine naive Zeit stillschweigend als UTC deuten, und aus „02:00 lokal"
  würde eine falsche Uhrzeit in der Oberfläche. Deshalb wird hier streng geprüft
  und ein Wert ohne Zone verworfen.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.core.logging import log_warning
from app.schemas.backups import BackupRunStatus, BackupStatusRead

logger = logging.getLogger("school_assistant.backups")

_MOUNT_HINT = (
    "Worker und Backend müssen das Volume worker-status gemeinsam benutzen; im Compose-Stand "
    "von heute hängt es nur am Worker."
)


def _unavailable(message: str) -> BackupStatusRead:
    return BackupStatusRead(available=False, message=message)


def _aware(value: object) -> datetime | None:
    """Liest einen ISO-Zeitstempel und verwirft alles ohne Zeitzone."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _text(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _boolean(payload: dict[str, object], key: str) -> bool | None:
    value = payload.get(key)
    return value if isinstance(value, bool) else None


def _integer(payload: dict[str, object], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(payload: dict[str, object], key: str) -> float | None:
    value = payload.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _names(payload: dict[str, object], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str)]


def _program_name(path: str | None) -> str | None:
    """Aus einem Container-Pfad nur den Dateinamen.

    Ein vollständiger Pfad gehört nicht in eine Antwort an den Browser: er sagt
    dem Leser nichts und deckt die Verzeichnisstruktur des Containers auf.
    """
    if path is None:
        return None
    return path.replace("\\", "/").rsplit("/", 1)[-1] or path


def _naive_timestamps(payload: dict[str, object]) -> list[str]:
    """Die Zeitstempel, die vorhanden sind, aber keine Zeitzone tragen."""
    missing: list[str] = []
    for key in ("last_started_at", "last_finished_at", "last_successful_at", "next_due_at"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip() and _aware(value) is None:
            missing.append(key)
    return missing


def read_backup_status(status_path: Path) -> BackupStatusRead:
    """Liest die Worker-Statusdatei und übersetzt nur den Backup-Teil."""
    try:
        raw = status_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _unavailable(f"Die Worker-Statusdatei wurde unter {status_path} nicht gefunden. {_MOUNT_HINT}")
    except OSError as error:
        return _unavailable(f"Die Worker-Statusdatei ist nicht lesbar: {error.strerror or type(error).__name__}.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _unavailable("Die Worker-Statusdatei ist gerade nicht als JSON lesbar; der Worker schreibt sie neu.")
    if not isinstance(payload, dict):
        return _unavailable("Die Worker-Statusdatei hat ein unerwartetes Format.")

    worker_status = _text(payload, "status")
    worker_database = _text(payload, "database")
    last_heartbeat = _aware(payload.get("last_heartbeat"))

    raw_backup = payload.get("backup")
    if not isinstance(raw_backup, dict):
        return BackupStatusRead(
            available=False,
            message="Der Worker meldet noch keinen Backup-Status; er läuft vermutlich noch ohne Backup-Runner.",
            worker_status=worker_status,
            worker_database=worker_database,
            last_heartbeat=last_heartbeat,
        )

    backup = BackupRunStatus(
        enabled=_boolean(raw_backup, "enabled") or False,
        runner_available=_boolean(raw_backup, "runner_available") or False,
        schedule=_text(raw_backup, "schedule"),
        timezone=_text(raw_backup, "timezone"),
        retention_days=_integer(raw_backup, "retention_days"),
        directory_writable=_boolean(raw_backup, "directory_writable"),
        pg_dump=_program_name(_text(raw_backup, "pg_dump_path")),
        last_started_at=_aware(raw_backup.get("last_started_at")),
        last_finished_at=_aware(raw_backup.get("last_finished_at")),
        last_successful_at=_aware(raw_backup.get("last_successful_at")),
        next_due_at=_aware(raw_backup.get("next_due_at")),
        last_duration_seconds=_number(raw_backup, "last_duration_seconds"),
        last_file=_text(raw_backup, "last_file"),
        last_size_bytes=_integer(raw_backup, "last_size_bytes"),
        kept_files=_integer(raw_backup, "kept_files"),
        removed_files=_names(raw_backup, "removed_files"),
        last_error=_text(raw_backup, "last_error"),
    )

    discarded = _naive_timestamps(raw_backup)
    message = "Der Backup-Status stammt aus der Statusdatei des Workers."
    if discarded:
        log_warning(logger, "backup_status_timestamps_dropped", fields=discarded)
        message += f" {len(discarded)} Zeitstempel ohne Zeitzone wurden verworfen."
    return BackupStatusRead(
        available=True,
        message=message,
        worker_status=worker_status,
        worker_database=worker_database,
        last_heartbeat=last_heartbeat,
        backup=backup,
    )
