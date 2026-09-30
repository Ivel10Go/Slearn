from pathlib import Path

from fastapi import APIRouter

from app.api.deps import AppSettings, CurrentUser
from app.schemas.backups import BackupStatusRead
from app.services.backups import read_backup_status

router = APIRouter(prefix="/api/v1/backups", tags=["backups"])


@router.get("/status", response_model=BackupStatusRead)
def backup_status(_user: CurrentUser, settings: AppSettings) -> BackupStatusRead:
    """Liest den Backup-Stand aus der Statusdatei des Workers. Nur lesend.

    Es gibt bewusst keinen Endpunkt, der ein Backup auslöst: `pg_dump` läuft im
    Worker nach seinem Zeitplan, und ein zweiter Weg dorthin wäre ein zweiter
    Zustand.

    Antwortet immer mit HTTP 200. Ist die Datei nicht erreichbar, steht das in
    ``available`` und ``message`` - das ist eine Konfigurationsfrage und kein
    Fehler dieses Aufrufs.
    """
    return read_backup_status(Path(settings.worker_status_path))
