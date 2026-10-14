"""Termine in Slearn: von Hand, aus einer Quelle und wieder nach draußen.

Neben dem üblichen Anlegen, Ändern und Löschen kommen hier drei Dinge dazu, die
alle aus derselben Frage stammen: **woher weiß die App, was wohin gehört?**

* **Lokale Änderungen werden markiert.** ``sync_state`` wechselt auf ``edited``,
  sobald jemand in der App einen Termin einer Gegenseite ändert. Ohne diese
  Markierung dürfte ein Abruf die Änderung beim nächsten Lauf überschreiben -
  und das ist genau der Fehler, den die Bidirektionalität verhindern soll. Der
  WebUntis-Import braucht das nicht: dort ist das Schulportal die Quelle der
  Wahrheit, hier ist es die App selbst.
* **Gräbstöne sind Grabsteine.** Ein gelöschter Termin einer Gegenseite wird
  nicht weggeschmissen, sondern auf ``deleted`` gesetzt, damit das ``DELETE``
  dort noch geschehen kann. Solche Zeilen tauchen in keiner Liste auf und sind
  per Id nicht abrufbar.
* **Der Export** ist eine abonnierbare ICS-Adresse mit einem eigenen Schlüssel,
  der jederzeit widerrufbar ist. Warum das kein Sitzungs-Cookie ist, steht im
  Docstring von ``app/services/calendar_feed.py``.

Ebenfalls hier und aus demselben Grund: die Einwilligung zum Schreiben. Sie
gehört nicht in ``integrations``, weil sie **nicht** an einer einzelnen
Integration hängt, sondern an der Frage, ob Slearn Termine in einen
Kalender schreiben darf - für alle zusammen.
"""

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import Select

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.api.helpers import normalized_datetime, owned_or_404
from app.core.time import resolve_timezone_name
from app.db.entities import SYNC_STATE_DELETED, SYNC_STATE_EDITED, SYNC_STATE_SYNCED, CalendarEvent, User
from app.schemas.academic import (
    CalendarEventCreate,
    CalendarEventRead,
    CalendarEventUpdate,
    CalendarFeedRead,
    CalendarWritingRead,
    CalendarWritingWrite,
)
from app.schemas.common import MessageResponse
from app.services import calendar_consent, calendar_feed

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])

#: Wie lange ein Kalender-Dienst den Abo-Schlüssel zwischenspeichern soll. Kurz
#: genug, dass ein Widerruf auffällt, lang genug, dass kein Abruf doppelt läuft.
FEED_MAX_AGE_SECONDS = 300


def _visible(query: Select[tuple[CalendarEvent]]) -> Select[tuple[CalendarEvent]]:
    """Blendet Gräbstöne aus - in **jeder** Liste."""
    return query.where(CalendarEvent.sync_state != SYNC_STATE_DELETED)


@router.get("/events", response_model=list[CalendarEventRead])
@router.get("", response_model=list[CalendarEventRead])
def list_events(
    current_user: CurrentUser,
    db: DatabaseSession,
    upcoming: bool = Query(default=True),
) -> list[CalendarEvent]:
    from app.core.time import utc_now

    query = select(CalendarEvent).where(CalendarEvent.user_id == current_user.id)
    if upcoming:
        query = query.where(CalendarEvent.end_time >= utc_now())
    return list(db.scalars(_visible(query).order_by(CalendarEvent.start_time)).all())


@router.post("/events", response_model=CalendarEventRead, status_code=status.HTTP_201_CREATED)
@router.post("", response_model=CalendarEventRead, status_code=status.HTTP_201_CREATED)
def create_event(payload: CalendarEventCreate, current_user: CurrentUser, db: DatabaseSession) -> CalendarEvent:
    values = payload.model_dump()
    values["start_time"] = normalized_datetime(values["start_time"])
    values["end_time"] = normalized_datetime(values["end_time"])
    if values["end_time"] < values["start_time"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="end_time must not precede start_time"
        )
    item = CalendarEvent(user_id=current_user.id, **values)
    db.add(item)
    try:
        db.commit()
        db.refresh(item)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Calendar event already exists")
    return item


@router.get("/events/{event_id}", response_model=CalendarEventRead)
def get_event(event_id: int, current_user: CurrentUser, db: DatabaseSession) -> CalendarEvent:
    item = owned_or_404(db, CalendarEvent, event_id, current_user.id)
    if item.is_tombstone:
        # Ein Grabstein ist eine Zeile, die auf ihr eigenes DELETE wartet. Für
        # die Oberfläche existiert der Termin nicht - 404 wie bei einer fremden
        # Id, denn beides heißt hier dasselbe: hier ist nichts.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    return item


@router.patch("/events/{event_id}", response_model=CalendarEventRead)
def update_event(
    event_id: int, payload: CalendarEventUpdate, current_user: CurrentUser, db: DatabaseSession
) -> CalendarEvent:
    item = owned_or_404(db, CalendarEvent, event_id, current_user.id)
    if item.is_tombstone:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    values = payload.model_dump(exclude_unset=True)
    for key in ("start_time", "end_time"):
        if key in values:
            values[key] = normalized_datetime(values[key])
    start = normalized_datetime(values.get("start_time", item.start_time))
    end = normalized_datetime(values.get("end_time", item.end_time))
    if start is not None and end is not None and end < start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="end_time must not precede start_time"
        )
    for key, value in values.items():
        setattr(item, key, value)
    _mark_local_change(item, db)
    try:
        db.commit()
        db.refresh(item)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Calendar event already exists")
    return item


@router.delete("/events/{event_id}", response_model=MessageResponse)
def delete_event(event_id: int, current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    item = owned_or_404(db, CalendarEvent, event_id, current_user.id)
    if item.sync_source_id is not None:
        # Der Termin gehört einer Gegenseite. Die Zeile bleibt als Grabstein
        # stehen, damit das ``DELETE`` dort noch geschehen kann; ein Abruf
        # entfernt sie danach. Von Hand angelegte Termine (``sync_source_id`` ist
        # ``None``) verschwinden sofort wie bisher.
        item.sync_state = SYNC_STATE_DELETED
        db.commit()
        return MessageResponse(message="Calendar event deleted; die Quelle wird beim nächsten Abruf nachgezogen.")
    db.delete(item)
    db.commit()
    return MessageResponse(message="Calendar event deleted")


def _mark_local_change(item: CalendarEvent, db: DatabaseSession) -> None:
    """Merkt sich, dass hier jemand etwas geändert hat.

    **Nur** bei Terminen, die zu einer Quelle gehören (``sync_source_id``), und
    **nur** wenn sie dort noch stehen (``sync_state == 'synced'``). Der Grund für
    das zweite Wenn ist wichtig: eine Änderung an einem Grabstein änderte nichts
    an der Lage, würde ihn aber wieder in die Liste zurückholen. Und das
    ``remote_etag`` bleibt **bewusst** stehen - es ist die Fassung, auf der sich
    die lokale Änderung bezieht, und genau damit vergleicht der Schreibvorgang
    später. Leeren hieße: nie eine Abweichung bemerken.
    """
    if item.sync_source_id is None or item.sync_state != SYNC_STATE_SYNCED:
        return
    item.sync_state = SYNC_STATE_EDITED
    db.flush()


# -- Einwilligung zum Schreiben -------------------------------------------


@router.get("/writing", response_model=CalendarWritingRead)
def read_writing(current_user: CurrentUser, db: DatabaseSession) -> CalendarWritingRead:
    return CalendarWritingRead(
        share_calendar_for_writing=calendar_consent.calendar_writing_allowed(db, current_user.id),
        preference_key=calendar_consent.WRITING_PREFERENCE_KEY,
        uses=list(calendar_consent.WRITING_USES),
    )


@router.put("/writing", response_model=CalendarWritingRead)
def update_writing(
    payload: CalendarWritingWrite,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> CalendarWritingRead:
    enabled = calendar_consent.set_calendar_writing(db, current_user.id, payload.enabled)
    db.commit()
    return CalendarWritingRead(
        share_calendar_for_writing=enabled,
        preference_key=calendar_consent.WRITING_PREFERENCE_KEY,
        uses=list(calendar_consent.WRITING_USES),
    )


# -- Abonnierbare ICS-Quelle -----------------------------------------------


@router.get("/feed", response_model=CalendarFeedRead)
def read_feed(current_user: CurrentUser, db: DatabaseSession) -> CalendarFeedRead:
    """Die Adresse, mit der sich der Kalender abonnieren lässt.

    Der Schlüssel steht in der Antwort. Das ist kein Versehen: nur die
    Eigentümerin bekommt diese Antwort, und ohne den Schlüssel könnte sie die
    Adresse nicht in ihren Kalender eintragen. Für jeden anderen ist er wertlos.
    """
    token = calendar_feed.feed_token(db, current_user.id)
    return CalendarFeedRead(
        url_path=calendar_feed.feed_path(token) if token else "",
        active=bool(token),
        message=(
            "Trage diese Adresse in deinem Kalender ein, um Slearn zu abonnieren."
            if token
            else "Es ist noch keine abonnierbare Adresse eingerichtet."
        ),
    )


@router.post("/feed", response_model=CalendarFeedRead, status_code=status.HTTP_201_CREATED)
def create_feed(current_user: CurrentUser, db: DatabaseSession) -> CalendarFeedRead:
    """Erzeugt einen neuen Abo-Schlüssel. Der alte ist ab sofort ungültig."""
    created = calendar_feed.rotate_feed_token(db, current_user.id)
    return CalendarFeedRead(
        url_path=created.url_path,
        active=True,
        rotated=True,
        message="Ein neuer Schlüssel ist erzeugt. Die alte Adresse funktioniert nicht mehr.",
    )


@router.get("/feed/{token}")
def read_feed_content(token: str, db: DatabaseSession, settings: AppSettings) -> Response:
    """Die ICS-Datei für einen Abo-Schlüssel - **ohne** Sitzung.

    Diese eine Route ist die Ausnahme von "alles unter ``/api/v1`` braucht eine
    Sitzung". Es muss so sein: ein abonnierender Kalender ist ein fremdes
    Programm, es hat kein Sitzungs-Cookie und keinen Browser.

    Der Ersatz ist ein eigener Schlüssel mit genau einem Recht, und das ist der
    ganze Grund, warum das vertretbar ist:

    * 32 Bytes Zufall, verglichen mit ``compare_digest``.
    * Nur diese eine Route liest er, und nur Termine.
    * Er wird bei jeder Neuerzeugung ungültig, und ``POST /feed`` ist ein
      Klick in den Einstellungen.
    * Er ist unabhängig vom Passwort: ein Widerruf erzwingt kein neues Passwort.

    Was er **nicht** kann: Lernprotokoll, Noten, Einstellungen oder irgendetwas
    anderes lesen. Das ist der Unterschied zu einer Sitzung im Aufruf, und
    deshalb ist es kein Session-Token, obwohl es in einer URL steht.
    """
    user_id = calendar_feed.user_for_token(db, token)
    if user_id is None:
        # 404 statt 401: es gibt keine Session, und „unbekannter Schlüssel" ist
        # die einzige Information, die jemand hier herausfinden darf.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diese Kalenderadresse ist nicht (mehr) gültig.",
        )
    body = calendar_feed.build_feed(db, user_id, timezone_name=_timezone_of(db, user_id, settings))
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={
            # Kurz, weil der Schlüssel widerrufbar ist: ein Kalender, der eine
            # Woche zwischenspeichert, merkt einen Widerruf erst danach.
            "Cache-Control": f"private, max-age={FEED_MAX_AGE_SECONDS}",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _timezone_of(db: DatabaseSession, user_id: int, settings: AppSettings) -> str:
    """Die Zeitzone des Kontos, an der die ``X-WR-TIMEZONE``-Zeile hängt."""
    row = db.get(User, user_id)
    return resolve_timezone_name(row.timezone if row is not None else None, settings.timezone)
