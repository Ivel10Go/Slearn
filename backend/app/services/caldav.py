"""CalDAV mit einem app-spezifischen Passwort - der Weg zu Apple Kalender.

Warum CalDAV und kein iCloud-Sonderweg
--------------------------------------

iCloud kennt CalDAV. Der Sonderweg über die private Web-API von Apple ist
undokumentiert, braucht ein Apple-ID-Passwort und bricht, sobald Apple etwas
ändert. CalDAV ist ein verbreiteter Standard, den Nextcloud, ownCloud, Fastmail,
Posteo und auch ``caldavical`` sprechen - derselbe Code trägt also weit über
Apple hinaus. Das app-spezifische Passwort ist genau das, was Apple dafür
ausdrücklich vorsieht: ein eigenes Passwort nur für Drittanwendungen, das sich
allein widerrufen lässt, ohne das eigentliche Apple-ID-Passwort zu ändern.

Der Ablauf ist CalDAV-Standard und drei Schritte:

``PROPFIND /`` mit ``Depth: 0``
    Gibt ``current-user-principal`` und ``current-user-privilege-set``
    zurück. Das ist der billigste Test, der Benutzerkennung, Passwort, Server
    **und** Schreibrecht in einem prüft.
``REPORT`` mit ``calendar-query``
    Liefert die Termine samt ``getetag`` und ``getlastmodified``. Ohne
    ``calendar-data`` kommt nur die Liste der Links; mit kommt der Inhalt dazu.
``PUT``/``DELETE`` mit ``If-Match``
    Schreibt bedingt. Genau hier entscheidet sich die Bidirektionalität: ein
    ``If-Match`` mit veraltetem ETag antwortet **412**, und der Abgleich meldet
    einen Konflikt, statt die fremde Änderung zu überschreiben.

Der ``If-Match``-Header ist hier nicht optional. CalDAV-Server sind freizügig:
ein ``PUT`` ohne Bedingung überschreibt bedingungslos, auch wenn gerade jemand
anders denselben Termin verschoben hat. Genau dieses Überschreiben ist der
Grund, weshalb der Adapter beim Ersetzen immer eine Bedingung setzt - nur beim
allerersten Anlegen gibt es nichts zu überschreiben.

Nicht verifiziert
-----------------

Getestet ist gegen **gemockte** HTTP-Antworten. Es gab keinen Apple-Account und
keinen echten CalDAV-Server; ``caldav.icloud.com``, die ``calendar-query``-
Antwort und die ``If-Match``-Semantik sind aus der Dokumentation (RFC 4791,
RFC 6638) abgeleitet, nicht live geprüft. Die unterschiedlichen Dialekte der
Server sind echtes Risiko: ein Server mit Posteo antwortet auf dieselbe
Anfrage nicht Byte für Byte gleich wie einer mit Nextcloud.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final
from urllib.parse import quote, urljoin, urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.core.time import as_utc, utc_now
from app.services.calendar_sync import (
    CalendarAuthError,
    CalendarConfigError,
    CalendarConflictError,
    CalendarSyncError,
    RemoteBatch,
    RemoteEvent,
)
from app.services.ical import ICalError, ICalEvent, build_calendar, parse_calendar

DAV_NS: Final = "DAV:"
CALDAV_NS: Final = "urn:ietf:params:xml:ns:caldav"

#: Apple's CalDAV-Endpunkt. Steht hier, damit der Standardpfad nicht in der
#: Oberfläche getippt werden muss - die Adresse bleibt trotzdem ein Konfigurations-
#: feld, weil Nextcloud, ownCloud und die meisten Maildienste woanders liegen.
ICLOUD_CALDAV_URL: Final = "https://caldav.icloud.com"
DEFAULT_CALENDAR_PATH: Final = "calendars/"

_PRIVILEGE_SET: Final = f"{{{DAV_NS}}}current-user-privilege-set"
_PRINCIPAL: Final = f"{{{DAV_NS}}}current-user-principal"
_RESPONSE: Final = f"{{{DAV_NS}}}response"
_HREF: Final = f"{{{DAV_NS}}}href"
_CALENDAR_DATA: Final = f"{{{CALDAV_NS}}}calendar-data"
_GET_ETAG: Final = f"{{{DAV_NS}}}getetag"
_GET_LAST_MODIFIED: Final = f"{{{DAV_NS}}}getlastmodified"
_PRIVILEGES: Final = f"{{{DAV_NS}}}privilege"
_ALL: Final = f"{{{DAV_NS}}}all"
#: Mit Namespace. Der Vergleich läuft gegen die Elementnamen aus der Antwort,
#: und die sind ``{DAV:}write`` - ein nacktes ``"write"`` findet dort nichts.
_WRITE: Final = f"{{{DAV_NS}}}write"
_WRITE_CONTENT: Final = f"{{{DAV_NS}}}write-content"

#: Wie lange der Verbindungstest warten darf.
DISCOVERY_TIMEOUT_SECONDS: Final = 10.0


class CalDavError(CalendarSyncError):
    """Der CalDAV-Server hat die Anfrage abgelehnt oder unlesbar geantwortet."""


@dataclass(frozen=True, slots=True)
class CalDavIdentity:
    """Was der Verbindungstest herausgefunden hat."""

    user: str
    can_write: bool
    calendar_path: str

    def describe(self) -> str:
        text = "Der CalDAV-Server ist erreichbar und das Passwort stimmt."
        if self.user:
            text += f" Angemeldet als {self.user}."
        if self.can_write:
            text += " Termine lassen sich dort anlegen und ändern."
        else:
            text += " Dort lassen sich keine Termine anlegen oder ändern."
        return text


class CalDavClient:
    """Liest und schreibt Termine über CalDAV."""

    kind = "caldav"
    supports_writing = True

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        calendar_path: str = DEFAULT_CALENDAR_PATH,
        timezone_name: str = "UTC",
        timeout_seconds: float = DISCOVERY_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = _normalize_base(base_url)
        self._username = username.strip()
        self._password = password
        self._calendar_path = calendar_path.strip() or DEFAULT_CALENDAR_PATH
        self._zone = _zone(timezone_name)
        self._timeout = timeout_seconds
        if not self._username:
            raise CalendarAuthError("Es fehlt der Benutzername für CalDAV, meist die Apple-ID.")
        if not self._password:
            raise CalendarAuthError("Es fehlt das app-spezifische Passwort für CalDAV.")
        self._client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            auth=httpx.BasicAuth(self._username, self._password),
        )

    def __enter__(self) -> CalDavClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- Verbindung ------------------------------------------------------

    def check_connection(self) -> str:
        """Echter Verbindungstest über ``PROPFIND /`` mit ``Depth: 0``."""
        return self._discover().describe()

    def identify(self) -> CalDavIdentity:
        """Wer angemeldet ist und was dort erlaubt ist - für ``check_integration_connection``."""
        return self._discover()

    def _discover(self) -> CalDavIdentity:
        response = self._send("PROPFIND", self._base_url, body=_discover_body(), headers={"Depth": "0"})
        try:
            root = ElementTree.fromstring(response.text)
        except ElementTree.ParseError as exc:
            raise CalDavError("Der CalDAV-Server hat keine lesbare Antwort geliefert.") from exc
        can_write = False
        for privilege_set in root.iter(_PRIVILEGE_SET):
            for privilege in privilege_set.iter(_PRIVILEGES):
                if {_ALL, _WRITE, _WRITE_CONTENT} & {child.tag for child in privilege}:
                    can_write = True
        principal = _text(root.find(f".//{_PRINCIPAL}"), _HREF) if root.find(f".//{_PRINCIPAL}") is not None else ""
        return CalDavIdentity(
            user=_last_segment(principal) or self._username,
            can_write=can_write,
            calendar_path=self._calendar_path,
        )

    # -- Lesen -----------------------------------------------------------

    def fetch_events(self, start: datetime, end: datetime) -> RemoteBatch:
        report = self._send(
            "REPORT",
            self._calendar_url(),
            body=_report_body(start, end),
            headers={"Depth": "1", "Content-Type": 'application/xml; charset="utf-8"'},
        )
        try:
            root = ElementTree.fromstring(report.text)
        except ElementTree.ParseError as exc:
            raise CalDavError("Der CalDAV-Server hat keine lesbare Antwort geliefert.") from exc

        events: list[RemoteEvent] = []
        skipped = 0
        for node in root.findall(_RESPONSE):
            href = _text(node, _HREF)
            etag = _text(node, _GET_ETAG) or None
            modified = _parse_http_date(_text(node, _GET_LAST_MODIFIED))
            payload = _text(node, _CALENDAR_DATA)
            if not payload:
                # Kein ``calendar-data`` in der Antwort: der Server hat uns nur
                # die Links geschickt. Ohne Inhalt gibt es nichts zu holen.
                skipped += 1
                continue
            parsed = _parse_events(payload, href, etag, modified, self._zone)
            events.extend(parsed.events)
            skipped += parsed.skipped
        return RemoteBatch(events=tuple(events), skipped=skipped)

    # -- Schreiben -------------------------------------------------------

    def push_event(self, event: RemoteEvent, etag: str | None) -> RemoteEvent:
        url = self._event_url(event.uid)
        headers: dict[str, str] = {"Content-Type": "text/calendar; charset=utf-8"}
        if etag:
            # Nur beim Ersetzen. Beim allerersten Anlegen gibt es nichts, was
            # überschrieben werden könnte, und eine Bedingung, die es noch
            # nicht gibt, würde das Anlegen verhindern.
            headers["If-Match"] = etag
        response = self._send("PUT", url, body=_build_ics(event), headers=headers)
        stored = (response.headers.get("etag") or "").strip()
        return RemoteEvent(
            uid=event.uid,
            title=event.title,
            start=event.start,
            end=event.end,
            description=event.description,
            all_day=event.all_day,
            etag=stored or etag,
            modified_at=utc_now(),
        )

    def delete_event(self, uid: str, etag: str | None) -> None:
        # Ein 404 gilt als erledigt, ein 412 ist ein Konflikt. Beides wird in
        # `_send` unterschieden.
        headers = {"If-Match": etag} if etag else {}
        self._send("DELETE", self._event_url(uid), headers=headers, ignore_missing=True)

    # -- HTTP ------------------------------------------------------------

    def _send(
        self,
        method: str,
        url: str,
        *,
        body: str | None = None,
        headers: dict[str, str] | None = None,
        ignore_missing: bool = False,
    ) -> httpx.Response:
        merged = {"Accept": "application/xml, text/xml, text/calendar"}
        merged.update(headers or {})
        try:
            response = self._client.request(method, url, content=body, headers=merged)
        except httpx.TimeoutException as exc:
            raise CalDavError("Zeitüberschreitung bei der Verbindung zum CalDAV-Server.") from exc
        except httpx.HTTPError as exc:
            raise CalDavError(f"Der CalDAV-Server ist nicht erreichbar: {exc}") from exc

        if ignore_missing and response.status_code == 404:
            return response
        if response.status_code in {401, 403}:
            # 401 heißt wörtlich: Benutzername oder Passwort stimmen nicht. Bei
            # iCloud ist das fast immer das Apple-ID-Passwort statt des
            # app-spezifischen - der häufigste Fehler in diesem Bereich.
            raise CalendarAuthError(
                "Der CalDAV-Server hat Benutzername oder Passwort abgelehnt. "
                "Bei iCloud ist das das app-spezifische Passwort, nicht das Apple-ID-Passwort."
            )
        if response.status_code == 404:
            raise CalendarConfigError(f"Der CalDAV-Server kennt '{urlparse(url).path}' nicht.")
        if response.status_code == 412:
            raise CalendarConflictError(
                "Der Termin wurde bei CalDAV zwischenzeitlich geändert; es wurde nichts überschrieben."
            )
        if response.status_code >= 400:
            raise CalDavError(f"Der CalDAV-Server hat die Anfrage abgelehnt (HTTP {response.status_code}).")
        return response

    def _calendar_url(self) -> str:
        return urljoin(f"{self._base_url}/", self._calendar_path.lstrip("/"))

    def _event_url(self, uid: str) -> str:
        base = self._calendar_url().rstrip("/")
        return f"{base}/{quote(uid, safe='')}.ics"


@dataclass(frozen=True, slots=True)
class _ParsedResource:
    events: tuple[RemoteEvent, ...]
    skipped: int


def _parse_events(
    payload: str,
    href: str,
    etag: str | None,
    modified: datetime | None,
    zone: ZoneInfo,
) -> _ParsedResource:
    """Ein ``calendar-data``-Block wird zu ``RemoteEvent``-Zeilen.

    Für die Identität wird die ``UID`` aus der Datei benutzt, nicht der Name im
    href: die UID ist über einen Umzug oder ein neues Konto hinweg stabil, der
    href nicht. Der href dient nur als Rückfall, wenn die Datei keine UID hat -
    und dann ist er wichtig, weil sonst jeder Termin dieselbe Identität bekäme.
    """
    try:
        calendar = parse_calendar(payload, zone)
    except ICalError as exc:
        raise CalDavError(f"Der CalDAV-Server liefert eine unlesbare Termindatei: {exc}") from exc

    events: list[RemoteEvent] = []
    for event in calendar.events:
        events.append(
            RemoteEvent(
                uid=event.uid or _name_from_href(href),
                title=event.summary,
                start=event.start,
                end=event.end,
                description=event.description,
                all_day=event.all_day,
                floating=event.floating,
                etag=etag,
                modified_at=modified or event.modified_at,
            )
        )
    return _ParsedResource(events=tuple(events), skipped=calendar.skipped)


def _build_ics(event: RemoteEvent) -> str:
    """Baut die Datei für einen ``PUT``.

    Die UID ist die fremde ``uid`` und wird **nicht** durch eine eigene ersetzt:
    ein Termin, der schon existiert, muss unter derselben Identität
    aktualisiert werden, sonst legt der Server eine zweite Kopie an.
    """
    return build_calendar(
        [
            ICalEvent(
                uid=event.uid,
                summary=event.title,
                start=event.start,
                end=event.end,
                description=event.description,
                all_day=event.all_day,
                modified_at=utc_now(),
            )
        ],
        calendar_name="Lernraum",
        timezone_name="UTC",
    )


def _normalize_base(base_url: str) -> str:
    cleaned = base_url.strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CalendarConfigError("Die CalDAV-Adresse muss eine vollständige http(s)-URL sein.")
    if parsed.query or parsed.fragment:
        raise CalendarConfigError("Die CalDAV-Adresse darf keine Abfrage und keinen Anker enthalten.")
    return cleaned


def _discover_body() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8" ?>'
        f'<d:propfind xmlns:d="{DAV_NS}">'
        f"<d:prop><d:current-user-principal/><d:current-user-privilege-set/></d:prop>"
        "</d:propfind>"
    )


def _report_body(start: datetime, end: datetime) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8" ?>'
        f'<c:calendar-query xmlns:d="{DAV_NS}" xmlns:c="{CALDAV_NS}">'
        "<d:prop><d:getetag/><d:getlastmodified/><c:calendar-data/></d:prop>"
        '<c:filter><c:comp-filter name="VCALENDAR">'
        '<c:comp-filter name="VEVENT">'
        f'<c:time-range start="{_ical_stamp(start)}" end="{_ical_stamp(end)}"/>'
        "</c:comp-filter></c:comp-filter></c:filter>"
        "</c:calendar-query>"
    )


def _ical_stamp(value: datetime) -> str:
    return as_utc(value).strftime("%Y%m%dT%H%M%SZ")


def _text(node: ElementTree.Element | None, tag: str) -> str:
    """Der Text des ersten passenden Kindes, leer wenn keins da ist."""
    if node is None:
        return ""
    found = node.find(f".//{tag}")
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def _last_segment(href: str) -> str:
    tail = href.rstrip("/").rsplit("/", 1)[-1]
    return quote(tail, safe="") if tail else ""


def _name_from_href(href: str) -> str:
    tail = _last_segment(href)
    return tail[: -len(".ics")] if tail.lower().endswith(".ics") else tail


def _parse_http_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def probe_caldav(
    *,
    base_url: str,
    username: str,
    password: str,
    calendar_path: str = DEFAULT_CALENDAR_PATH,
    timeout_seconds: float = DISCOVERY_TIMEOUT_SECONDS,
) -> CalDavIdentity:
    """Bequemlichkeit für Aufrufer, die nur die Verbindung prüfen wollen."""
    with CalDavClient(
        base_url=base_url,
        username=username,
        password=password,
        calendar_path=calendar_path,
        timeout_seconds=timeout_seconds,
    ) as client:
        return client.identify()


__all__ = [
    "DEFAULT_CALENDAR_PATH",
    "ICLOUD_CALDAV_URL",
    "CalDavClient",
    "CalDavError",
    "CalDavIdentity",
    "DISCOVERY_TIMEOUT_SECONDS",
    "probe_caldav",
]
