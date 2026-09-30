"""Erinnert planmaessig, statt nur auf einen Knopfdruck.

**Warum das nicht der Thread aus ``integration_scheduler.py`` ist.** Die Datei
ist in dieser Welle gesperrt, und es gibt darueber hinaus einen sachlichen
Grund fuer zwei Threads statt fuer einen gemeinsamen:

* **Zwei Taktarten.** Der WebUntis-Abruf laeuft im Tagesabstand (Vorgabe 24 h),
  eine Erinnerung an eine heute faellige Aufgabe nicht. Ein gemeinsamer Thread
  mit einem Intervall muesste das kleinere der beiden nehmen - und damit den
  Stundenplanabruf fuenfmal am Tag aufrufen, was genau der Grund ist, warum er
  nicht im Minutentakt laeuft.
* **Zwei Fehlergrenzen.** Der WebUntis-Thread startet nur mit
  ``WEBUNTIS_SYNC_ENABLED``; Erinnerungen muessen laufen, auch wenn niemand ein
  Schulportal angebunden hat. Haette man sie an diesen Thread gehaengt, waeren
  sie in einem frisch aufgesetzten Stack nie angekommen.

Beide Threads sind bewusst gleich gebaut - zweistufig, ein ``stop_event``, ein
nicht blockierender Lock, und ein Lauf, an dem der Thread nicht sterben darf.
Damit ist der Zusammenschluss spaeter mechanisch: ein gemeinsames
``PeriodicJob``-Modul, in das beide wandern.

Der Testgrund fuer die Zweistufigkeit ist derselbe wie dort: ``run_scheduled_reminders``
ist eine reine Funktion ueber einer Session und laesst sich mit einem festen
``now`` aufrufen, waehrend der Thread keine Uhr und kein Netz braucht.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import log_event, log_warning
from app.core.time import as_utc, utc_now
from app.db.session import get_session_factory
from app.services.notifications import SweepOutcome, active_reminder_users, sweep_for_user

logger = logging.getLogger("school_assistant.notification_scheduler")

#: Der erste Lauf wartet nicht das ganze Intervall. Nach einem Neustart soll die
#: Erinnerung nicht bis zum Abend warten - ``reminder_time`` und ``dedupe_key``
#: sorgen dafuer, dass daraus kein zweiter Versand am selben Tag wird.
STARTUP_DELAY_SECONDS: Final[float] = 20.0

#: Die Obergrenze fuer das Intervall steht am Feld
#: ``Settings.reminder_interval_seconds`` (``ge=60``, ``le=86400``) und nicht
#: hier. Ein Konstruktor, der nach oben begrenzt, waere gegenueber einem
#: ausdruecklich uebergebenen Intervall still und wuerde den Tests eine
#: Sockelregel aufzwingen, die es in der Produktion nicht gibt.

ReminderJob = Callable[..., "ScheduledReminderReport"]
SessionFactory = Callable[[], Session]


@dataclass(frozen=True, slots=True)
class ScheduledReminderReport:
    """Ergebnis eines kompletten Laufs ueber alle Benutzer."""

    considered: int
    created: int
    delivered: int
    push_failed: int
    outcomes: tuple[SweepOutcome, ...] = ()

    def describe(self) -> str:
        return (
            f"{self.created} Erinnerung(en) fuer {self.considered} aktive Benutzer angelegt, "
            f"{self.delivered} davon an einen Push-Dienst gegeben."
        )


def run_scheduled_reminders(
    *,
    db: Session,
    settings: Settings,
    now: datetime | None = None,
) -> ScheduledReminderReport:
    """Ein Lauf ueber alle aktiven Benutzer.

    **Nutzerisolation ist die wichtigste Eigenschaft dieser Schleife.** Ein
    Fehler bei Benutzer A wird in dessen ``message`` geschrieben und der Lauf
    macht mit B weiter; nichts wird nach aussen gegeben. Ein ``SQLAlchemyError``
    bricht auch das nicht ab, sondern rollt zurueck und zaehlt den Benutzer als
    gescheitert - der Thread darf daran nicht sterben.
    """
    moment = as_utc(now or utc_now())
    outcomes: list[SweepOutcome] = []
    for user_id, timezone_name in active_reminder_users(db):
        outcomes.append(_sweep_one(db, settings, user_id, timezone_name, moment=moment))
    report = ScheduledReminderReport(
        considered=len(outcomes),
        created=sum(item.created for item in outcomes),
        delivered=sum(item.delivered for item in outcomes),
        push_failed=sum(item.push_failed for item in outcomes),
        outcomes=tuple(outcomes),
    )
    log_event(
        logger,
        "reminder_sweep_completed",
        summary=report.describe(),
        considered=report.considered,
        created=report.created,
        delivered=report.delivered,
        push_failed=report.push_failed,
    )
    return report


def _sweep_one(
    db: Session,
    settings: Settings,
    user_id: int,
    timezone_name: str,
    *,
    moment: datetime,
) -> SweepOutcome:
    """Ein Benutzer. Der Fehlerpfad ist derselbe wie im WebUntis-Scheduler."""
    try:
        return sweep_for_user(db, user_id, timezone_name, settings=settings, now=moment)
    except SQLAlchemyError as error:
        db.rollback()
        log_warning(
            logger,
            "reminder_sweep_database_error",
            user_id=user_id,
            error_type=type(error).__name__,
        )
        return SweepOutcome(
            user_id=user_id,
            created=0,
            skipped_duplicate=0,
            delivered=0,
            push_failed=0,
            push_attempted=False,
            message="Die Datenbank war beim Erinnern nicht erreichbar.",
        )
    except Exception as error:
        db.rollback()
        log_warning(
            logger,
            "reminder_sweep_failed",
            user_id=user_id,
            error_type=type(error).__name__,
        )
        return SweepOutcome(
            user_id=user_id,
            created=0,
            skipped_duplicate=0,
            delivered=0,
            push_failed=0,
            push_attempted=False,
            message=f"Der Lauf ist fehlgeschlagen: {error}",
        )


class ReminderScheduler:
    """Hintergrund-Thread, der die Erinnerungen prueft.

    Der Thread bekommt seine Arbeit ueber ``job`` und seine Sitzung ueber
    ``session_factory`` gereicht. Beides ist in den Tests eine Attrappe, damit
    weder Uhr noch Netz ins Spiel kommen.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        interval_seconds: float | None = None,
        session_factory: SessionFactory | None = None,
        job: ReminderJob | None = None,
    ) -> None:
        self._settings = settings
        self._interval = float(
            interval_seconds if interval_seconds is not None else settings.reminder_interval_seconds
        )
        self._new_session: SessionFactory = session_factory if session_factory is not None else self._default_session
        self._job: ReminderJob = job if job is not None else run_scheduled_reminders
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
        self._thread = threading.Thread(target=self._loop, name="reminder-scheduler", daemon=True)
        self._thread.start()
        log_event(logger, "reminder_scheduler_started", interval_seconds=self._interval)

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None
        log_event(logger, "reminder_scheduler_stopped")

    def try_run_once(self) -> ScheduledReminderReport | None:
        """Fuehrt einen Lauf aus, sofern nicht gerade einer laeuft.

        ``None`` heisst: ein anderer Lauf hat den Lock, es gab also keinen
        Parallel-Lauf. Ein zweiter Sweep zur selben Zeit wuerde zwar ueber den
        ``dedupe_key`` nichts doppelt anlegen, aber zwei gleichzeitige
        Datenbanktransaktionen auf denselben Zeilen sind trotzdem etwas, das man
        nicht haben will.
        """
        if not self._lock.acquire(blocking=False):
            log_event(logger, "reminder_scheduler_busy")
            return None
        try:
            return self._execute()
        finally:
            self._lock.release()

    def _execute(self) -> ScheduledReminderReport:
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
            # Erst danach die volle Intervallzeit, sonst liefe der Lauf im
            # Sekundentakt, solange die erste Wartezeit kurz war.
            delay = self._interval
        log_event(logger, "reminder_scheduler_loop_stopped")

    def _safe_run_once(self) -> None:
        try:
            self.try_run_once()
        except Exception as error:
            # Der Thread darf an einem Lauf nicht sterben, sonst blieben die
            # Erinnerungen fuer immer aus.
            log_warning(
                logger,
                "reminder_scheduler_run_failed",
                error_type=type(error).__name__,
                exc_info=True,
            )
