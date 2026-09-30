from datetime import datetime

from pydantic import Field

from app.schemas.common import UTCResponseModel


class BackupRunStatus(UTCResponseModel):
    """Was der Worker über den Backup-Runner weiß.

    Alles ist optional: ein Worker, der den Runner nicht kennt, schreibt
    ``null``. Die Oberfläche zeigt dann „unbekannt" und nicht etwa „nie".
    """

    enabled: bool = False
    runner_available: bool = False
    schedule: str | None = None
    timezone: str | None = None
    retention_days: int | None = None
    directory_writable: bool | None = None
    #: Nur der Dateiname des Programms, nie ein voller Pfad aus dem Container.
    pg_dump: str | None = None
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_successful_at: datetime | None = None
    next_due_at: datetime | None = None
    last_duration_seconds: float | None = None
    last_file: str | None = None
    last_size_bytes: int | None = None
    kept_files: int | None = None
    removed_files: list[str] = Field(default_factory=list)
    last_error: str | None = None


class BackupStatusRead(UTCResponseModel):
    """Antwort von ``GET /api/v1/backups/status``.

    ``available`` heisst: die Statusdatei des Workers war lesbar **und** enthielt
    einen Backup-Block. Fehlt sie, ist das eine Konfigurationsfrage (geteiltes
    Volume) und kein Fehler des Laufs - deshalb HTTP 200 mit Klartext statt 503.
    """

    available: bool
    message: str
    worker_status: str | None = None
    worker_database: str | None = None
    last_heartbeat: datetime | None = None
    backup: BackupRunStatus | None = None
