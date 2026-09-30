import json
import logging
import os
import signal
import threading
from pathlib import Path

import psycopg

from worker.backup import BackupConfigurationError, BackupRunner, disabled_backup_status, load_backup_config
from worker.env import normalize_database_url, positive_float, required_value, utc_now

LOGGER = logging.getLogger("school-assistant.worker")
DEFAULT_STATUS_PATH = "/var/lib/school-assistant/worker-status/worker-status.json"

#: Wie oft der Backup-Runner nach einem faelligen Termin sieht. Der Wert ist
#: absichtlich kleiner als der Intervall der Datenbankpruefung: der Dump soll nicht
#: erst beim naechsten DB-Check auffallen, sondern innerhalb einer Minute.
DEFAULT_BACKUP_TICK_SECONDS = 60.0

#: So lange wartet der Worker beim Beenden auf einen laufenden Dump. Der Wert
#: bleibt unter `stop_grace_period` von Compose, damit der Container die Zeit hat.
BACKUP_SHUTDOWN_JOIN_SECONDS = 20.0

#: Solange startet der Runner nicht: der Block steht in der Statusdatei, damit
#: ein Leser nie auf einen fehlenden Schluessel trifft.
BACKUP_PENDING_MESSAGE = "Der Backup-Runner startet noch nicht."


class StatusFile:
    def __init__(self, path: Path, heartbeat_interval: float) -> None:
        self.path = path
        self.heartbeat_interval = heartbeat_interval
        self.lock = threading.Lock()
        started_at = utc_now().isoformat()
        self.data: dict[str, object] = {
            "version": 1,
            "status": "starting",
            "database": "unchecked",
            "pid": os.getpid(),
            "heartbeat_interval_seconds": heartbeat_interval,
            "started_at": started_at,
            "last_heartbeat": started_at,
            "last_database_check": None,
            "last_error": None,
            # Additiv: `status`, `database` und `last_heartbeat` bleiben unberuehrt,
            # weil genau diese drei Felder der Healthcheck auswertet. `backup`
            # beschreibt nur den Runner und kann den Worker nie ungesund machen.
            "backup": disabled_backup_status(BACKUP_PENDING_MESSAGE),
        }

    def update(self, **values: object) -> None:
        with self.lock:
            self.data.update(values)
            self.data["last_heartbeat"] = utc_now().isoformat()
            self.write()

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_name(f".{self.path.name}.tmp")
        temporary_path.write_text(json.dumps(self.data, sort_keys=True), encoding="utf-8")
        temporary_path.replace(self.path)


def heartbeat_loop(status: StatusFile, stop_event: threading.Event) -> None:
    while not stop_event.wait(status.heartbeat_interval):
        try:
            status.update()
        except OSError as error:
            LOGGER.error("Der Status-Heartbeat ist fehlgeschlagen: %s", type(error).__name__)


def backup_loop(
    runner: BackupRunner | None,
    status: StatusFile,
    stop_event: threading.Event,
    tick_interval: float,
    disabled_reason: str | None = None,
) -> None:
    """Prueft regelmaessig, ob eine Sicherung faellig ist, und schreibt sie.

    Zwei Regeln sind hier nicht verhandelbar:

    * Der Thread fasst weder ``status`` noch ``database`` an. Ein Backup, das
      scheitert, ist ein anderes Problem als eine Datenbank, die nicht erreichbar
      ist - der Worker darf dadurch nicht unhealthy werden.
    * Jeder Fehler bleibt in diesem Thread. Ein Ausfall des Backups darf die
      Datenbankpruefung nicht beenden.
    """
    if runner is None:
        status.update(backup=disabled_backup_status(disabled_reason or BACKUP_PENDING_MESSAGE))
        LOGGER.warning("Der Backup-Runner laeuft nicht: %s", disabled_reason or BACKUP_PENDING_MESSAGE)
        return
    while not stop_event.is_set():
        unexpected_error: str | None = None
        if not stop_event.wait(tick_interval):
            try:
                if runner.is_due():
                    LOGGER.info("Backup-Termin erreicht, pg_dump laeuft.")
                    runner.run_once()
            except Exception as error:  # der Thread darf daran nicht sterben
                unexpected_error = f"Unerwarteter Fehler im Backup-Lauf: {type(error).__name__}"
                LOGGER.error("%s", unexpected_error, exc_info=True)
        try:
            status.update(backup=runner.status(error=unexpected_error))
        except OSError as error:
            LOGGER.error("Der Backup-Status liess sich nicht schreiben: %s", type(error).__name__)


def check_database(database_url: str) -> None:
    with psycopg.connect(
        normalize_database_url(database_url),
        autocommit=True,
        connect_timeout=5,
    ) as connection:
        result = connection.execute("SELECT 1").fetchone()
    if result is None or result[0] != 1:
        raise RuntimeError("Unerwartete Antwort der Datenbankpruefung")


def build_backup_runner(database_url: str) -> tuple[BackupRunner | None, str | None, float]:
    """Baut den Runner oder liefert den deutschen Grund, warum er aus bleibt.

    Eine kaputte Backup-Konfiguration darf den Worker **nicht** beenden: der
    Healthcheck wertet genau drei Felder aus, und ``pg_dump``-Aerger gehoert nicht
    in diese Pruefung. Deshalb endet der Fehler hier als Log-Eintrag.
    """
    tick_interval = positive_float("BACKUP_TICK_SECONDS", DEFAULT_BACKUP_TICK_SECONDS)
    try:
        return load_backup_config(database_url), None, tick_interval
    except BackupConfigurationError as error:
        return None, str(error), tick_interval


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    database_url = required_value("DATABASE_URL")
    poll_interval = positive_float("WORKER_POLL_INTERVAL_SECONDS", 300)
    heartbeat_interval = positive_float("WORKER_HEARTBEAT_INTERVAL_SECONDS", 30)
    health_max_age = positive_float("WORKER_HEALTH_MAX_AGE_SECONDS", 120)
    if health_max_age <= heartbeat_interval:
        raise ValueError("WORKER_HEALTH_MAX_AGE_SECONDS muss groesser als WORKER_HEARTBEAT_INTERVAL_SECONDS sein")

    status_path = Path(os.getenv("WORKER_STATUS_PATH", DEFAULT_STATUS_PATH))
    status = StatusFile(status_path, heartbeat_interval)
    status.update()

    stop_event = threading.Event()

    def request_shutdown(signal_number: int, _frame: object) -> None:
        LOGGER.info("Beenden-Signal erhalten: %s", signal.Signals(signal_number).name)
        stop_event.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    heartbeat = threading.Thread(
        target=heartbeat_loop,
        args=(status, stop_event),
        daemon=True,
        name="worker-heartbeat",
    )
    heartbeat.start()

    backup_runner, backup_reason, backup_tick = build_backup_runner(database_url)
    backups = threading.Thread(
        target=backup_loop,
        args=(backup_runner, status, stop_event, backup_tick, backup_reason),
        daemon=True,
        name="worker-backup",
    )
    backups.start()
    LOGGER.info("Worker gestartet; Hintergrundaufgaben: Datenbankpruefung, Heartbeat, Backup-Runner")

    try:
        while not stop_event.is_set():
            try:
                check_database(database_url)
            except Exception as error:
                status.update(
                    status="degraded",
                    database="unhealthy",
                    last_database_check=utc_now().isoformat(),
                    last_error=type(error).__name__,
                )
                LOGGER.error("Die Datenbankpruefung ist fehlgeschlagen: %s", type(error).__name__)
            else:
                status.update(
                    status="running",
                    database="healthy",
                    last_database_check=utc_now().isoformat(),
                    last_error=None,
                )
                LOGGER.info("Die Datenbankpruefung war erfolgreich")
            stop_event.wait(poll_interval)
    finally:
        stop_event.set()
        status.update(status="stopping", database="stopping")
        heartbeat.join(timeout=min(heartbeat_interval, 5))
        backups.join(timeout=min(backup_tick, BACKUP_SHUTDOWN_JOIN_SECONDS))
        status.update(status="stopped", database="disconnected")
        LOGGER.info("Worker beendet")


if __name__ == "__main__":
    main()
