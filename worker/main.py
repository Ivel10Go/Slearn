import json
import logging
import math
import os
import signal
import threading
from datetime import datetime, timezone
from pathlib import Path

import psycopg

LOGGER = logging.getLogger("school-assistant.worker")
DEFAULT_STATUS_PATH = "/var/lib/school-assistant/worker-status/worker-status.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def positive_float(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def required_value(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} is required")
    return value.strip()


def normalize_database_url(value: str) -> str:
    normalized = value.strip()
    scheme, separator, remainder = normalized.partition("://")
    if not separator:
        return normalized
    if scheme == "postgres" or scheme.startswith("postgresql+"):
        scheme = "postgresql"
    return f"{scheme}://{remainder}"


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
            LOGGER.error("Status heartbeat failed: %s", type(error).__name__)


def check_database(database_url: str) -> None:
    with psycopg.connect(
        normalize_database_url(database_url),
        autocommit=True,
        connect_timeout=5,
    ) as connection:
        result = connection.execute("SELECT 1").fetchone()
    if result is None or result[0] != 1:
        raise RuntimeError("Unexpected database health response")


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
        raise ValueError("WORKER_HEALTH_MAX_AGE_SECONDS must exceed WORKER_HEARTBEAT_INTERVAL_SECONDS")

    status_path = Path(os.getenv("WORKER_STATUS_PATH", DEFAULT_STATUS_PATH))
    status = StatusFile(status_path, heartbeat_interval)
    status.update()

    stop_event = threading.Event()

    def request_shutdown(signal_number: int, _frame: object) -> None:
        LOGGER.info("Shutdown signal received: %s", signal.Signals(signal_number).name)
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
    LOGGER.info("Worker started; no background jobs are registered")

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
                LOGGER.error("Database health check failed: %s", type(error).__name__)
            else:
                status.update(
                    status="running",
                    database="healthy",
                    last_database_check=utc_now().isoformat(),
                    last_error=None,
                )
                LOGGER.info("Database health check passed")
            stop_event.wait(poll_interval)
    finally:
        stop_event.set()
        status.update(status="stopping", database="stopping")
        heartbeat.join(timeout=min(heartbeat_interval, 5))
        status.update(status="stopped", database="disconnected")
        LOGGER.info("Worker stopped")


if __name__ == "__main__":
    main()
