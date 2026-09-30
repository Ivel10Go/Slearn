"""Benachrichtigungen unter ``/api/v1/notifications``.

Neun Routen, alle mit Session und Nutzertrennung:

* ``GET    /settings``        - die gespeicherte Lage, mit den Zahlen fuer die Oberflaeche
* ``PATCH  /settings``        - Hauptschalter, Kanaele, Uhrzeit, Ruhezeiten
* ``GET    /vapid``           - der oeffentliche Schluessel, falls einer eingerichtet ist
* ``GET    /subscriptions``   - die angemeldeten Endgeraete
* ``POST   /subscriptions``   - ein Endgeraet anmelden oder erneuern
* ``DELETE /subscriptions/{id}`` - ein Endgeraet abmelden
* ``GET    /``                - was die offene Seite anzeigen soll (der lokale Weg)
* ``POST   /read``            - als gelesen markieren
* ``POST   /reminders/run``   - einen Lauf jetzt ausloesen (der Knopf in den Einstellungen)

Zwei Entscheidungen, die man beim Lesen der Routen wiedererkennen sollte:

**``/vapid`` antwortet 200, auch wenn nichts eingerichtet ist.** Ein Server ohne
VAPID-Schluesselpaar ist kein Fehler, sondern die Auspraegung, in der der
lokale Weg arbeitet. Ein 503 wuerde die Oberflaeche in einen Fehlerzweig
schicken, obwohl alles in Ordnung ist.

**Der Knopf ``/reminders/run`` ist der einzige Weg, planmaessige Arbeit von
aussen anzustossen** - genau wie beim WebUntis-Abruf. Er geht unabhaengig von
``REMINDER_SCHEDULER_ENABLED``, damit die Einstellungen auch auf einem Stack
sagen koennen, was passieren wuerde, auf dem der Thread aus ist.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.core.config import require_web_push_private_key, require_web_push_subject
from app.core.logging import log_event
from app.core.time import as_utc, resolve_timezone_name, utc_now
from app.db.entities import Notification
from app.schemas.common import MessageResponse
from app.schemas.notifications import (
    MarkReadRequest,
    MarkReadResult,
    NotificationRead,
    PushSubscriptionRead,
    PushSubscriptionWrite,
    ReminderOutcomeRead,
    ReminderPreferencesRead,
    ReminderPreferencesWrite,
    ReminderRunRead,
    VapidKeyRead,
)
from app.services import notifications as notification_service
from app.services import web_push
from app.services.notification_scheduler import run_scheduled_reminders

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])
logger = logging.getLogger("school_assistant.api.notifications")


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def _unavailable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


def _timezone(current_user: CurrentUser, settings: AppSettings) -> str:
    return resolve_timezone_name(current_user.timezone, settings.timezone)


# --- Die Einstellungen ------------------------------------------------------


@router.get("/settings", response_model=ReminderPreferencesRead)
def read_settings(current_user: CurrentUser, db: DatabaseSession, settings: AppSettings) -> ReminderPreferencesRead:
    """Die Erinnerungseinstellungen, **unveraendert gelesen**.

    Es wird hier nichts geschrieben: auch ein Benutzer, der die Seite nur
    oeffnet, soll keine Zeile bekommen. Das ist derselbe Grund, aus dem es beim
    Lernfortschritt ein ``PUT`` statt eines ``PATCH`` gibt - nur umgekehrt.
    """
    prefs = notification_service.read_preferences(db, current_user.id)
    return notification_service.preferences_response(prefs, timezone_name=_timezone(current_user, settings))


@router.patch("/settings", response_model=ReminderPreferencesRead)
def update_settings(
    payload: ReminderPreferencesWrite,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> ReminderPreferencesRead:
    """Hauptschalter, Kanaele, Uhrzeit und Ruhezeiten aendern.

    Jedes Feld ist optional, und **fehlend heisst unveraendert**. Die Antwort
    ist die gespeicherte Lage, nicht das, was geschickt wurde - die Oberflaeche
    uebernimmt sie, damit die Anzeige nicht von einem ablehnenden Normalfall
    abweichen kann.
    """
    quiet = notification_service.quiet_hours_from_write(payload.quiet_hours)
    try:
        prefs = notification_service.write_preferences(
            db,
            current_user.id,
            enabled=payload.enabled,
            channels=(
                {str(channel): value for channel, value in payload.channels.items()}
                if payload.channels is not None
                else None
            ),
            reminder_time=(
                notification_service.parse_clock(payload.reminder_time) if payload.reminder_time is not None else None
            ),
            quiet=quiet,
        )
        db.commit()
    except notification_service.NotificationSettingsError as exc:
        db.rollback()
        raise _unprocessable(str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise _unprocessable("Die Erinnerungseinstellungen konnten nicht gespeichert werden.") from exc
    log_event(logger, "notification_settings_updated", user_id=current_user.id, enabled=prefs.enabled)
    return notification_service.preferences_response(prefs, timezone_name=_timezone(current_user, settings))


# --- Der oeffentliche Schluessel --------------------------------------------


@router.get("/vapid", response_model=VapidKeyRead)
def read_vapid(settings: AppSettings) -> VapidKeyRead:
    """Der Application Server Key fuer ``pushManager.subscribe``.

    **200 statt 503**, wenn nichts eingestellt ist: das ist kein Fehler, sondern
    die Konfiguration, in der die Erinnerung ueber die offene Seite laeuft.

    **503**, wenn ein Schluessel eingetragen ist, aber nicht brauchbar ist. Das
    ist ein Unterschied, den man nicht verwischen darf: ein fehlender Schluessel
    heisst "Push ist aus", ein kaputter heisst "Push ist eingerichtet und tut
    trotzdem nicht, was er soll" - und die zweite Meldung waere sonst fuer
    immer als "Push ist aus" getarnt.
    """
    try:
        keypair = web_push.resolve_vapid_keypair(settings)
    except web_push.WebPushConfigError as exc:
        raise _unavailable(f"Das VAPID-Schlüsselpaar ist unbrauchbar: {exc}") from exc
    except RuntimeError:
        # `require_web_push_private_key`: nichts eingetragen.
        return VapidKeyRead(configured=False, public_key=None, subject=None)
    try:
        subject: str | None = require_web_push_subject(settings)
    except RuntimeError:
        subject = None
    return VapidKeyRead(configured=True, public_key=keypair.public_key_b64, subject=subject)


# --- Die Abonnements --------------------------------------------------------


def _endpoint_hint(endpoint: str) -> str:
    """Der Endpunkt, bis auf den Dienst erkennbar.

    Der Endpunkt enthaelt ein geheimes Pfad-Stueck des Anbieters. Er wird
    niemals vollstaendig ausgeliefert und niemals geloggt - das
    Lueckenzeichen steht fuer alles ab dem Pfad.
    """
    teile = urlsplit(endpoint)
    if not teile.netloc:
        return "…"
    return f"{teile.scheme}://{teile.netloc}/…"


@router.get("/subscriptions", response_model=list[PushSubscriptionRead])
def list_subscriptions(current_user: CurrentUser, db: DatabaseSession) -> list[PushSubscriptionRead]:
    """Die angemeldeten Endgeraete dieses Nutzers."""
    rows = notification_service.subscriptions_of(db, current_user.id)
    return [
        PushSubscriptionRead(
            id=row.id,
            endpoint_hint=_endpoint_hint(row.endpoint),
            user_agent=row.user_agent,
            failures=row.failures,
            last_success_at=as_utc(row.last_success_at) if row.last_success_at else None,
            last_failure_at=as_utc(row.last_failure_at) if row.last_failure_at else None,
            last_error=row.last_error,
            created_at=as_utc(row.created_at),
        )
        for row in rows
    ]


@router.post("/subscriptions", response_model=PushSubscriptionRead, status_code=status.HTTP_201_CREATED)
def create_subscription(
    payload: PushSubscriptionWrite,
    request: Request,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> PushSubscriptionRead:
    """Ein Endgeraet anmelden.

    Ohne Schluesselpaar geht es nicht: ein Abonnement, das der Server nicht
    bedienen kann, waere eine Zeile, die nur so aussieht, als ginge es. Deshalb
    **503** statt 201 - mit einem Text, der sagt, was zu tun ist.
    """
    try:
        require_web_push_private_key(settings)
        require_web_push_subject(settings)
    except RuntimeError as exc:
        raise _unavailable("Web-Push ist auf diesem Server nicht eingerichtet.") from exc
    if not payload.endpoint.startswith("https://"):
        raise _unprocessable("Das Endgerät muss einen HTTPS-Endpunkt melden.")
    try:
        web_push.check_subscription_keys(payload.p256dh, payload.auth)
    except web_push.WebPushError as exc:
        raise _unprocessable(f"Die Schlüssel des Endgeräts sind unbrauchbar: {exc}") from exc
    row = notification_service.register_subscription(
        db,
        current_user.id,
        endpoint=payload.endpoint,
        p256dh=payload.p256dh,
        auth=payload.auth,
        user_agent=(payload.user_agent or request.headers.get("user-agent") or "")[:320] or None,
    )
    db.commit()
    db.refresh(row)
    log_event(logger, "push_subscription_registered", user_id=current_user.id, subscription_id=row.id)
    return PushSubscriptionRead(
        id=row.id,
        endpoint_hint=_endpoint_hint(row.endpoint),
        user_agent=row.user_agent,
        failures=row.failures,
        last_success_at=as_utc(row.last_success_at) if row.last_success_at else None,
        last_failure_at=as_utc(row.last_failure_at) if row.last_failure_at else None,
        last_error=row.last_error,
        created_at=as_utc(row.created_at),
    )


@router.delete("/subscriptions/{subscription_id}", response_model=MessageResponse)
def delete_subscription(
    subscription_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> MessageResponse:
    """Ein Endgeraet abmelden. Eine fremde Id gibt 404, nicht 403."""
    row = notification_service.remove_subscription(db, current_user.id, subscription_id)
    if row is None:
        raise _not_found("Endgerät nicht gefunden")
    db.commit()
    log_event(logger, "push_subscription_removed", user_id=current_user.id, subscription_id=subscription_id)
    return MessageResponse(message="Endgerät abgemeldet")


# --- Was die offene Seite anzeigt ------------------------------------------


@router.get("", response_model=list[NotificationRead])
def list_pending(
    current_user: CurrentUser,
    db: DatabaseSession,
    after: int | None = Query(default=None, ge=0),
    limit: int = Query(default=notification_service.MAX_PENDING_PER_READ, ge=1, le=50),
) -> list[NotificationRead]:
    """Die Erinnerungen, die noch niemand gezeigt hat.

    Das ist der **lokale Weg**, und er ist der Hauptpfad: er braucht kein
    Abonnement, keinen Push-Dienst und keinen Schluesselpaar. Die Seite pollt
    diese Liste, zeigt sie ueber die ``Notification``-API der Seite und meldet
    sie danach als gelesen.

    ``after`` ist ein Id-Zeiger, kein Zeitstempel: die Ids sind aufsteigend, und
    ein Zeitstempel muesste in der Zone des Nutzers mit dem Server
    verrechnet werden.
    """
    rows = notification_service.pending_notifications(db, current_user.id, after=after, limit=limit)
    return [_read_notification(row) for row in rows]


def _read_notification(row: Notification) -> NotificationRead:
    return NotificationRead(
        id=row.id,
        kind=row.kind,  # type: ignore[arg-type]
        title=row.title,
        body=row.body,
        url=row.url,
        target_id=row.target_id,
        push_status=row.push_status,  # type: ignore[arg-type]
        created_at=as_utc(row.created_at),
        read_at=as_utc(row.read_at) if row.read_at else None,
    )


@router.post("/read", response_model=MarkReadResult)
def mark_notifications_read(
    payload: MarkReadRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> MarkReadResult:
    """Erinnerungen als gelesen markieren.

    Eine **fremde Id gibt 404**: dasselbe Muster wie bei einem fremden Dokument.
    Eine Liste mit einer fremden Id zu mischen und die guten trotzdem zu
    markieren wuerde verraten, dass es die fremde gibt - die Antwort waere dann
    "2 von 3 markiert" und damit eine Auskunft ueber fremde Zeilen.
    """
    for notification_id in payload.ids:
        row = db.get(Notification, notification_id)
        if row is None or row.user_id != current_user.id:
            raise _not_found("Erinnerung nicht gefunden")
    read = notification_service.mark_read(db, current_user.id, payload.ids)
    db.commit()
    log_event(logger, "notifications_marked_read", user_id=current_user.id, read=read)
    return MarkReadResult(read=read)


# --- Der Knopf --------------------------------------------------------------


@router.post("/reminders/run", response_model=ReminderRunRead)
def run_reminders(
    request: Request,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> ReminderRunRead:
    """Einen Lauf jetzt ausloesen.

    Der einzige Weg, planmaessige Arbeit von aussen zu starten - und er geht
    **unabhaengig** von ``REMINDER_SCHEDULER_ENABLED``, damit die
    Einstellungen auf einem Stack, auf dem der Thread aus ist, trotzdem zeigen
    koennen, was passieren wuerde. Der Lauf gilt dabei nur diesem Aufrufer: es
    wird nichts an der Einstellung geaendert.
    """
    moment = utc_now()
    report = run_scheduled_reminders(db=db, settings=settings, now=moment)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise _unavailable("Die Erinnerungen konnten nicht gespeichert werden.") from exc
    scheduler = getattr(request.app.state, "reminder_scheduler", None)
    interval = float(scheduler.interval_seconds) if scheduler is not None else settings.reminder_interval_seconds
    running = bool(scheduler is not None and scheduler.is_running())
    return ReminderRunRead(
        considered=report.considered,
        created=report.created,
        delivered=report.delivered,
        push_failed=report.push_failed,
        scheduler_running=running,
        interval_seconds=interval,
        outcomes=[
            ReminderOutcomeRead(
                user_id=item.user_id,
                created=item.created,
                delivered=item.delivered,
                push_failed=item.push_failed,
                push_attempted=item.push_attempted,
                message=item.message,
            )
            for item in report.outcomes
        ],
        message=report.describe(),
    )
