"""Der Kalender von Slearn als abonnierbare iCalendar-Quelle.

Der Gegenlauf zu den Importen: Termine, die in der App entstehen, verlassen sie
wieder als ICS-Datei, die sich in Google Kalender, Apple Kalender, Outlook oder
einen Nextcloud-Kalender eintragen lässt.

Der Zugang ist ein eigener Schlüssel, kein Sitzungs-Cookie
----------------------------------------------------------

Ein Abo funktioniert so, dass ein fremder Kalender die Adresse regelmäßig
abruft - ohne Browser, ohne Anmeldung, oftmals im Hintergrund. Dafür braucht es
etwas in der URL, und das ist hier ein **eigener Schlüssel** mit genau einem
Recht: diese Kalenderdatei lesen.

Das ist nicht dasselbe wie eine Sitzung im Aufruf, und der Unterschied ist der
Punkt:

* Ein Schlüssel kann **jederzeit widerrufen werden**, ohne das Passwort zu ändern
  und ohne irgendeine andere Sitzung anzufassen.
* Er gilt nur für ``/api/v1/calendar/feed/{token}``. Für alles andere ist er
  wertlos - ``/api/v1/calendar`` und die Integrationen verlangen weiterhin eine
  Sitzung.
* Er steht nicht in Cookies, nicht im Verlauf der Oberfläche und wird nie
  angezeigt, außer die Oberfläche zeigt ihn auf ausdrücklichen Wunsch.
* Er wird bei jeder Neuerzeugung ungültig, und die alte Adresse liefert dann
  404 statt Daten.

**Deshalb wird hier ausdrücklich kein Sitzungs-Cookie benutzt.** Eine
Sitzung im Aufruf bedeutet, dass jeder, der die Adresse sieht, den vollen
Slearn hat - Termine, Noten, Lernprotokoll. Ein Abo-Schlüssel bedeutet nur
einen Kalender.

Wie die Zeiten hinausgehen
--------------------------

Zeitpunkte als **UTC mit ``Z``**, ganztägige Termine als ``VALUE=DATE`` mit
exklusivem ``DTEND``. Damit steht an jedem Termin eine Zone, und der Weg
zurück braucht keine Annahme - dieselbe Regel wie beim Import, nur umgekehrt.

**Was nicht exportiert wird:** Gräbstöne (``sync_state = 'deleted'``). Sie sind
Zeilen, die auf ihr ``DELETE`` in einer Gegenseite warten; sie stehen nicht in
der Oberfläche und stehen hier erst recht nicht.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import as_utc
from app.db.entities import SYNC_STATE_DELETED, CalendarEvent, UserSettings
from app.services.calendar_sync import window_bounds
from app.services.ical import ICalEvent, build_calendar

#: Der Schlüssel hat 32 Bytes Zufall - nicht weniger. Ein kurzer Schlüssel in
#: einer URL, die jeder Kalender im Verlauf mitschreibt, ist eine Einladung.
FEED_TOKEN_BYTES: Final = 32
FEED_TOKEN_KEY: Final = "calendar_feed_token"
DEFAULT_CALENDAR_NAME: Final = "Slearn"
DEFAULT_FEED_WEEKS: Final = 12
MAX_FEED_WEEKS: Final = 52


@dataclass(frozen=True, slots=True)
class FeedToken:
    """Der Schlüssel und die Adresse, unter der er funktioniert."""

    token: str
    url_path: str

    def describe(self) -> str:
        return "Die abonnierbare Adresse steht bereit. Sie ändert sich, wenn du einen neuen Schlüssel erzeugst."


def feed_token(db: Session, user_id: int) -> str:
    """Der vorhandene Schlüssel, oder ``""``, wenn noch keiner erzeugt wurde."""
    row = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if row is None or not isinstance(row.preferences, dict):
        return ""
    value = row.preferences.get(FEED_TOKEN_KEY)
    return value.strip() if isinstance(value, str) else ""


def rotate_feed_token(db: Session, user_id: int) -> FeedToken:
    """Erzeugt einen neuen Schlüssel. Der alte ist ab sofort ungültig.

    Der Ablauf ist bewusst *nur* Neuerzeugen und nicht "anzeigen und
    wiederverwenden": ein Schlüssel, den man schon einmal gesehen hat, ist
    genau der, der in einer Sicherung oder in einer fremden Kalenderkonfiguration
    steht.
    """
    token = secrets.token_urlsafe(FEED_TOKEN_BYTES)
    row = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if row is None:
        row = UserSettings(user_id=user_id)
        db.add(row)
    preferences: dict[str, Any] = dict(row.preferences) if isinstance(row.preferences, dict) else {}
    preferences[FEED_TOKEN_KEY] = token
    row.preferences = preferences
    db.commit()
    return FeedToken(token=token, url_path=feed_path(token))


def feed_path(token: str) -> str:
    return f"/api/v1/calendar/feed/{token}"


def user_for_token(db: Session, token: str) -> int | None:
    """Die Nutzerkennung zu einem Schlüssel, oder ``None``.

    Gesucht wird in der Datenbank, nicht in Python. Ein Abo wird bei jedem
    Kalender-Dienst regelmäßig abgerufen; das Durchsuchen aller
    Einstellungszeilen im Speicher wäre bei jedem Abruf eine Volltextsuche über
    fremde Geheimnisse. Der Vergleich bleibt in SQL, und ````as_string()`` geht
    auf beiden Dialekten, die hier laufen (JSONB auf Postgres, JSON1 auf SQLite).

    Bewusst **ohne** eigene Tabelle je Nutzer: es gibt nur eine Frage zu
    beantworten - zu wem gehört dieser Schlüssel - und die steht in
    ``settings.preferences``. Eine Tabelle wäre eine zweite Wahrheit über
    dieselbe Sache plus ein Löschproblem, wenn ein Konto verschwindet.
    """
    candidate = token.strip()
    if len(candidate) < 16:
        # Kein Aufruf der Datenbank für einen offensichtlich zu kurzen Schlüssel.
        return None
    row = db.scalar(
        select(UserSettings.user_id).where(UserSettings.preferences[FEED_TOKEN_KEY].as_string() == candidate)
    )
    return int(row) if row is not None else None


def build_feed(
    db: Session,
    user_id: int,
    *,
    timezone_name: str,
    today: date | None = None,
    weeks: int = DEFAULT_FEED_WEEKS,
    calendar_name: str = DEFAULT_CALENDAR_NAME,
) -> str:
    """Baut die ICS-Datei für einen Nutzer.

    Das Fenster ist größer als beim Abruf (zwölf statt vier Wochen, weil ein
    Abo auch in der Zukunft liegen soll) und es ist **symmetrisch**: von vier
    Wochen zurück bis vier Wochen voraus. Nur die Zukunft zu exportieren hieße,
    dass ein gerade importierter Termin, der gestern war, beim nächsten Abruf
    aus der Quelle verschwände.
    """
    zone = _zone(timezone_name)
    reference = today or date.today()
    span = max(1, min(weeks, MAX_FEED_WEEKS))
    window_start = reference - timedelta(weeks=4)
    window_end = reference + timedelta(weeks=span)
    scope_start, scope_end = window_bounds(window_start, window_end, zone)

    rows = db.scalars(
        select(CalendarEvent)
        .where(
            CalendarEvent.user_id == user_id,
            CalendarEvent.sync_state != SYNC_STATE_DELETED,
            CalendarEvent.end_time >= scope_start,
            CalendarEvent.start_time <= scope_end,
        )
        .order_by(CalendarEvent.start_time, CalendarEvent.id)
    ).all()

    events: list[ICalEvent] = []
    for row in rows:
        start = as_utc(row.start_time)
        events.append(
            ICalEvent(
                uid=f"lernraum-{row.id}",
                summary=row.title,
                start=start,
                end=as_utc(row.end_time),
                description=row.description,
                all_day=row.all_day,
                modified_at=as_utc(row.updated_at),
            )
        )
    return build_calendar(events, calendar_name, timezone_name)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def feed_window(today: date, weeks: int = DEFAULT_FEED_WEEKS) -> tuple[datetime, datetime]:
    """Das Fenster in UTC - für Tests, die überprüfen wollen, was exportiert wird."""
    span = max(1, min(weeks, MAX_FEED_WEEKS))
    zone = _zone("UTC")
    return window_bounds(today - timedelta(weeks=4), today + timedelta(weeks=span), zone)


__all__ = [
    "DEFAULT_CALENDAR_NAME",
    "DEFAULT_FEED_WEEKS",
    "FEED_TOKEN_BYTES",
    "FEED_TOKEN_KEY",
    "MAX_FEED_WEEKS",
    "FeedToken",
    "build_feed",
    "feed_path",
    "feed_token",
    "feed_window",
    "rotate_feed_token",
    "user_for_token",
]
