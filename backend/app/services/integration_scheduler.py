"""Holt die WebUntis-Daten planmäßig statt nur auf einen Knopfdruck.

Die Aufteilung ist absichtlich zweistufig, damit die Tests keinen Zeitgeber
brauchen:

* ``run_scheduled_integration_syncs`` ist eine reine Funktion über einer
  Session. Sie entscheidet je Integration, ob der Abruf fällig ist, holt die
  Daten und schreibt Fehler in ``last_error``.
* ``IntegrationSyncScheduler`` ist der dünne Hintergrund-Thread, der genau
  diese Funktion aufruft. Er kennt weder Tabelle noch Zeitfenster.

Der Scheduler läuft im Backend und nicht im Worker: der Abruf braucht den
WebUntis-Adapter, das Entschlüsseln der Zugangsdaten und den Import in die
Datenbank - allesamt Code hinter ``backend/app``, den der Worker bewusst nicht
sieht. Ihm eine zweite, eigene Ausprägung davon zu geben, hieße denselben
Ablauf an zwei Stellen zu pflegen.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, require_integration_encryption_key
from app.core.crypto import decrypt_credentials
from app.core.logging import log_event, log_warning
from app.core.time import as_utc, resolve_timezone_name, utc_now
from app.db.entities import SyncSource, User
from app.db.session import get_session_factory
from app.services.integrations import (
    record_integration_sync_error,
    run_integration_sync,
)
from app.services.timetable_import import DEFAULT_SYNC_WEEKS

logger = logging.getLogger("school_assistant.integration_scheduler")

#: Nur diese Quelle wird planmäßig geholt. Der Dokumentabruf bleibt beim Knopf,
#: weil er ein lokales Verzeichnis betrifft und kein Zeitfenster kennt.
SCHEDULED_KIND: Final[str] = "webuntis"

#: Der erste Lauf wartet nicht das ganze Intervall. Nach einem Neustart soll der
#: Plan zügig wieder da sein; ``last_sync_at`` verhindert, dass daraus ein
#: zweiter Abruf am selben Tag wird.
STARTUP_DELAY_SECONDS: Final[float] = 30.0

SyncJob = Callable[..., "ScheduledSyncReport"]
SessionFactory = Callable[[], Session]


@dataclass(frozen=True, slots=True)
class ScheduledSyncOutcome:
    """Was mit einer einzelnen Integration passiert ist."""

    source_id: int
    user_id: int
    ok: bool
    skipped: bool
    message: str


@dataclass(frozen=True, slots=True)
class ScheduledSyncReport:
    """Ergebnis eines kompletten Laufs über alle Benutzer."""

    considered: int
    synced: int
    skipped: int
    failed: int
    outcomes: tuple[ScheduledSyncOutcome, ...] = ()

    def describe(self) -> str:
        return (
            f"{self.synced} von {self.considered} aktiven WebUntis-Integrationen geholt "
            f"({self.skipped} waren noch aktuell, {self.failed} fehlgeschlagen)."
        )


def scheduled_sync_is_due(source: SyncSource, now: datetime, interval_seconds: float) -> bool:
    """Gilt der planmäßige Abruf für diese Integration als fällig?

    Ohne ``last_sync_at`` ist er es immer, danach erst wieder, wenn das
    Intervall abgelaufen ist. Genau diese Prüfung hält einen zweiten Lauf am
    selben Tag vom Schulportal fern; der manuelle Knopf fragt sie nicht ab.
    """
    if source.last_sync_at is None:
        return True
    # ``as_utc`` ist nötig, weil SQLite den Zeitstempel ohne Zeitzone liefert.
    return as_utc(now) - as_utc(source.last_sync_at) >= timedelta(seconds=interval_seconds)


def active_scheduled_sources(db: Session) -> list[tuple[SyncSource, User]]:
    """Alle WebUntis-Quellen aktiver Benutzer, in stabiler Reihenfolge.

    Die Reihenfolge nach ``user_id`` ist Absicht: ein Fehler trifft damit immer
    denselben Benutzer, und die Tests sehen eine vorhersehbare Abfolge.
    """
    rows = db.execute(
        select(SyncSource, User)
        .join(User, User.id == SyncSource.user_id)
        .where(
            SyncSource.source_type == SCHEDULED_KIND,
            SyncSource.enabled.is_(True),
            User.is_active.is_(True),
        )
        .order_by(SyncSource.user_id, SyncSource.id)
    ).all()
    return [(row[0], row[1]) for row in rows]


def run_scheduled_integration_syncs(
    *,
    db: Session,
    settings: Settings,
    now: datetime | None = None,
    weeks: int = DEFAULT_SYNC_WEEKS,
) -> ScheduledSyncReport:
    """Holt für jeden Benutzer mit aktiver WebUntis-Integration die Daten.

    Nutzerisolation ist die wichtigste Eigenschaft dieser Schleife: ein Fehler
    bei einer Integration wird in ``last_error`` geschrieben und der Lauf macht
    mit dem nächsten Benutzer weiter. Nichts wird hier nach außen gegeben.
    """
    moment = now or utc_now()
    interval = settings.webuntis_sync_interval_seconds
    outcomes: list[ScheduledSyncOutcome] = []
    for source, user in active_scheduled_sources(db):
        outcomes.append(_sync_one(db, settings, source, user, moment=moment, interval=interval, weeks=weeks))
    report = ScheduledSyncReport(
        considered=len(outcomes),
        synced=sum(1 for item in outcomes if item.ok and not item.skipped),
        skipped=sum(1 for item in outcomes if item.skipped),
        failed=sum(1 for item in outcomes if not item.ok),
        outcomes=tuple(outcomes),
    )
    log_event(
        logger,
        "integration_sync_completed",
        summary=report.describe(),
        considered=report.considered,
        synced=report.synced,
        skipped=report.skipped,
        failed=report.failed,
        interval_seconds=interval,
    )
    return report


def _sync_one(
    db: Session,
    settings: Settings,
    source: SyncSource,
    user: User,
    *,
    moment: datetime,
    interval: float,
    weeks: int,
) -> ScheduledSyncOutcome:
    """Holt eine einzelne Integration und hält sie in der Zeile fehlerfrei."""
    source_id = source.id
    user_id = source.user_id
    if not scheduled_sync_is_due(source, moment, interval):
        log_event(logger, "integration_sync_skipped", source_id=source_id, user_id=user_id)
        return ScheduledSyncOutcome(
            source_id=source_id,
            user_id=user_id,
            ok=True,
            skipped=True,
            message="Der letzte Abruf ist noch aktuell.",
        )

    try:
        key = require_integration_encryption_key(settings)
        credentials = decrypt_credentials(source.credentials, key) if source.credentials else {}
        config = {str(field): str(value) for field, value in (source.configuration or {}).items()}
        run = run_integration_sync(
            db=db,
            source=source,
            config=config,
            credential_keys=sorted(credentials),
            settings=settings,
            timezone_name=resolve_timezone_name(user.timezone, settings.timezone),
            weeks=weeks,
        )
    except SQLAlchemyError as error:
        # Ein Datenbankfehler ist kein Fehler der Integration, deshalb wird
        # nichts geschrieben: der Import hat dann ohnehin nichts verändert.
        db.rollback()
        log_warning(
            logger,
            "integration_sync_database_error",
            source_id=source_id,
            user_id=user_id,
            error_type=type(error).__name__,
        )
        return ScheduledSyncOutcome(
            source_id=source_id,
            user_id=user_id,
            ok=False,
            skipped=False,
            message="Die Datenbank war beim planmäßigen Abruf nicht erreichbar.",
        )
    except Exception as error:
        # Fehlender Schlüssel oder nicht lesbare Zugangsdaten landen über
        # denselben Weg in ``last_error`` wie ein Verbindungsfehler.
        message = str(error) or type(error).__name__
        record_integration_sync_error(db, source, message)
        log_warning(
            logger,
            "integration_sync_failed",
            source_id=source_id,
            user_id=user_id,
            error_type=type(error).__name__,
        )
        return ScheduledSyncOutcome(
            source_id=source_id,
            user_id=user_id,
            ok=False,
            skipped=False,
            message=message,
        )

    if not run.ok:
        log_event(
            logger,
            "integration_sync_rejected",
            source_id=source_id,
            user_id=user_id,
            status=run.status,
        )
    else:
        log_event(logger, "integration_sync_succeeded", source_id=source_id, user_id=user_id)
    return ScheduledSyncOutcome(
        source_id=source_id,
        user_id=user_id,
        ok=run.ok,
        skipped=False,
        message=run.message,
    )


class IntegrationSyncScheduler:
    """Hintergrund-Thread, der die aktiven WebUntis-Integrationen abfragt.

    Der Thread bekommt seine Arbeit über ``job`` und seine Sitzung über
    ``session_factory`` gereicht. Beides ist in den Tests eine Attrappe, damit
    weder Netz noch Uhrzeitleiter ins Spiel kommen.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        interval_seconds: float | None = None,
        session_factory: SessionFactory | None = None,
        job: SyncJob | None = None,
    ) -> None:
        self._settings = settings
        self._interval = float(
            interval_seconds if interval_seconds is not None else settings.webuntis_sync_interval_seconds
        )
        self._new_session: SessionFactory = session_factory if session_factory is not None else self._default_session
        self._job: SyncJob = job if job is not None else run_scheduled_integration_syncs
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def interval_seconds(self) -> float:
        return self._interval

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _default_session(self) -> Session:
        return get_session_factory(self._settings)()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="webuntis-sync-scheduler", daemon=True)
        self._thread.start()
        log_event(logger, "integration_scheduler_started", interval_seconds=self._interval)

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None
        log_event(logger, "integration_scheduler_stopped")

    def try_run_once(self) -> ScheduledSyncReport | None:
        """Führt einen Lauf aus, sofern nicht gerade einer läuft.

        ``None`` heißt: ein anderer Lauf hat den Lock, es gab also keinen
        Parallel-Lauf. Das ist die zweite Hälfte der Idempotenz - die erste ist
        ``last_sync_at``.
        """
        if not self._lock.acquire(blocking=False):
            log_event(logger, "integration_scheduler_busy")
            return None
        try:
            return self._execute()
        finally:
            self._lock.release()

    def _execute(self) -> ScheduledSyncReport:
        db = self._new_session()
        try:
            report = self._job(db=db, settings=self._settings)
            db.commit()
            return report
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _loop(self) -> None:
        delay = min(self._interval, STARTUP_DELAY_SECONDS)
        while not self._stop.is_set():
            if self._stop.wait(delay):
                break
            self._safe_run_once()
            # Erst danach die volle Intervallzeit, sonst liefe der Abruf im
            # Sekundentakt, solange die erste Wartezeit kurz war.
            delay = self._interval
        log_event(logger, "integration_scheduler_loop_stopped")

    def _safe_run_once(self) -> None:
        try:
            self.try_run_once()
        except Exception as error:
            # Der Thread darf an einem Lauf nicht sterben, sonst bliebe der
            # planmäßige Abruf für immer aus.
            log_warning(
                logger,
                "integration_scheduler_run_failed",
                error_type=type(error).__name__,
                exc_info=True,
            )
