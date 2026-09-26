"""Minimaler WebUntis-Client auf Basis der JSON-RPC-Schnittstelle.

WebUntis spricht kein REST, sondern ein JSON-RPC-2.0-Protokoll unter
``{base_url}/WebUntis/jsonrpc.do``. Ein Aufruf besteht aus einem ``id``, einer
``method`` und ``params``; die Antwort trägt die Nutzlast in ``result``.

Der Login liefert eine ``sessionId``, die als ``JSESSIONID``-Cookie für
Folgeaufrufe mitgeschickt werden muss. WebUntis weist ausdrücklich darauf hin,
dass eine Anwendung sich so bald wie möglich wieder abmeldet, weil der Server
die Sitzung nach kurzer Leerlaufzeit selbst räumt.

Der Stundenplan kommt aus ``getTimetable``. Die Methode ist nicht Teil der
öffentlichen REST-Dokumentation, sie wird aber von der Web-App selbst benutzt;
sie erwartet ein ``options``-Objekt mit der ``element``-Referenz auf die Person
und die Feldauswahl für Klassen, Räume, Fächer und Lehrer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Final
from urllib.parse import quote, urlparse

import httpx

_RPC_PATH: Final = "/jsonrpc.do"
_CLIENT_ID: Final = "school-assistant/1.0"
_JSONRPC_HEADERS: Final = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}

_AUTH_FAILED_CODES: Final = frozenset({-8520, -8521, -8527})
_SESSION_CODES: Final = frozenset({-8509, -8510})
_TIMEOUT_CODES: Final = frozenset({-8522})
_INVALID_SCHOOL_CODES: Final = frozenset({-8500})
# -7002: Dem Login-Konto ist kein Element zugeordnet, für das ein Stundenplan
# abgefragt werden könnte (z. B. ein Verwaltungskonto ohne Personenbezug).
_NO_SUCH_ELEMENT_CODES: Final = frozenset({-7002})

# WebUntis typisiert die Elemente eines Stundenplans numerisch. Der Wert aus dem
# Login (``personType``) ist genau diese Typnummer, deshalb wird er direkt
# weitergegeben: 1 Klasse, 2 Lehrer, 3 Fach, 4 Raum, 5 Schueler.
_ELEMENT_CLASS: Final = 1
_ELEMENT_TEACHER: Final = 2
_ELEMENT_STUDENT: Final = 5
_TIMETABLE_ELEMENT_TYPES: Final = frozenset({_ELEMENT_CLASS, _ELEMENT_TEACHER, _ELEMENT_STUDENT})

# Felder, die WebUntis pro Klassen-, Raum-, Fach- und Lehrereintrag zurückgibt.
_ENTITY_FIELDS: Final = ["id", "name", "longname", "externalkey"]

# WebUntis markiert ausgefallene Stunden nicht mit einem eigenen Feld, sondern
# setzt in ``code`` den Grund. Live geprueft am Lessing-Gymnasium: dort steht
# woertlich "cancelled". Die Einheit selbst bleibt vollstaendig in der Antwort.
CANCELLED_CODE: Final = "cancelled"

_TIMETABLE_SHOW_OPTIONS: Final = {
    "showLsText": True,
    "showStudentgroup": True,
    "showLsNumber": True,
    "showSubstText": True,
    "showInfo": True,
    "showBooking": True,
}


class WebUntisError(RuntimeError):
    """Basis für alle Fehler aus der WebUntis-Kommunikation."""


class WebUntisConfigError(WebUntisError):
    """Die hinterlegte Konfiguration ist unbrauchbar (z. B. unbekannte Schule)."""


class WebUntisAuthError(WebUntisError):
    """Benutzername oder Passwort wurden vom Server abgelehnt."""


@dataclass(frozen=True, slots=True)
class WebUntisIdentity:
    """Ergebnis eines erfolgreichen Logins."""

    person_id: int | None = None
    person_type: int | None = None
    klasse_id: int | None = None

    def describe(self) -> str:
        parts: list[str] = []
        if self.person_id is not None:
            parts.append(f"Person-ID {self.person_id}")
        if self.klasse_id is not None:
            parts.append(f"Klassen-ID {self.klasse_id}")
        if self.person_type is not None:
            parts.append(f"Typ {self.person_type}")
        return ", ".join(parts) if parts else "Session erfolgreich aufgebaut"


@dataclass(frozen=True, slots=True)
class WebUntisSession:
    """Eine offene Sitzung: die ``sessionId`` plus die ermittelte Identität."""

    session_id: str
    identity: WebUntisIdentity


@dataclass(frozen=True, slots=True)
class WebUntisTimetable:
    """Ein geladener Stundenplan samt Fenster und Zahl verworfener Einträge."""

    entries: tuple[WebUntisLessonEntry, ...]
    window_start: date
    window_end: date
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class WebUntisEntity:
    """Ein Klassen-, Raum-, Fach- oder Lehrereintrag aus dem Stundenplan.

    ``name`` ist das kurze Kürzel aus dem Plan (``Geo``, ``322``), ``longname``
    die ausgeschriebene Form (``Geographie - Ek``).
    """

    id: int
    name: str
    longname: str | None = None

    def display(self, prefer_long: bool = False) -> str:
        """Wählt die Anzeigeform.

        ``prefer_long`` ist für Fächer und Lehrkräfte richtig, dort ist die
        ausgeschriebene Form hilfreicher. Bei Räumen ist es die Nummer, weil
        WebUntis dort teils den Klassen- oder Gebäudenamen als ``longname``
        führt und der eigentliche Raumcode im ``name`` steht.
        """
        if prefer_long:
            return self.longname or self.name or str(self.id)
        return self.name or self.longname or str(self.id)

    def short(self) -> str | None:
        """Das Kürzel, sofern WebUntis eines mitliefert.

        Bei Fächern ist ``name`` genau das Kürzel (``F`` zu ``Französisch``),
        bei Räumen die Nummer und bei Lehrkräften eine Abkürzung. Liefert die
        Antwort kein ``name``-Feld, steht dort nur die Ersatz-ID, und die wäre
        als Kürzel sinnlos - deshalb wird sie nicht gemeldet.
        """
        if not self.name or self.name == str(self.id):
            return None
        return self.name


@dataclass(frozen=True, slots=True)
class WebUntisLessonEntry:
    """Eine Unterrichtseinheit in Schul-Ortszeit.

    ``start`` und ``end`` sind reine Uhrzeiten ohne Datum und ohne Zeitzone, weil
    WebUntis Schul-Ortszeit liefert. Die Umrechnung in UTC ist Aufgabe des
    Importers, der die Zeitzone des Nutzerkontos kennt.
    """

    external_id: str
    day: date
    start: time
    end: time
    activity_type: str
    classes: tuple[WebUntisEntity, ...] = ()
    teachers: tuple[WebUntisEntity, ...] = ()
    subjects: tuple[WebUntisEntity, ...] = ()
    rooms: tuple[WebUntisEntity, ...] = ()
    lesson_text: str | None = None
    is_cancelled: bool = False


def build_rpc_url(base_url: str, school: str) -> str:
    """Leitet die JSON-RPC-URL aus Basis-URL und Schulnamen ab.

    Akzeptiert die üblichen Eingaben: ``https://schule.webuntis.com``,
    mit oder ohne Slash, sowie eine bereits auf ``/WebUntis`` zeigende URL.
    """
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WebUntisConfigError("Die WebUntis-Adresse muss eine vollständige http(s)-URL sein.")
    path = parsed.path.rstrip("/")
    if path and not path.lower().endswith("/webuntis"):
        raise WebUntisConfigError(
            "Die WebUntis-Adresse darf nur den Server enthalten, "
            f"gefunden wurde aber der Pfad '{parsed.path}'."
        )
    if not path:
        path = "/WebUntis"
    return f"{parsed.scheme}://{parsed.netloc}{path}{_RPC_PATH}?school={quote(school, safe='')}"


class WebUntisClient:
    """Einloggen, Verbindung prüfen, Stundenplan laden, wieder abmelden."""

    def __init__(
        self,
        base_url: str,
        school: str,
        username: str,
        password: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._url = build_rpc_url(base_url, school)
        self._school = school
        self._username = username
        self._password = password
        self._timeout = timeout_seconds
        self._client = httpx.Client(timeout=timeout_seconds, follow_redirects=True)

    def __enter__(self) -> WebUntisClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def check_connection(self) -> WebUntisIdentity:
        """Meldet an, prüft die Zugangsdaten und meldet sich sofort wieder ab."""
        session = self.login()
        self._logout()
        return session.identity

    def fetch_timetable(self, start: date, end: date) -> WebUntisTimetable:
        """Lädt den Stundenplan des angemeldeten Kontos für einen Zeitraum.

        Der Login liefert zugleich die Personen-ID, die als ``element`` an
        WebUntis übergeben wird. WebUntis kennt zu dieser Person genau einen
        Plan, deshalb wird kein Wochenoffset geschickt.
        """
        session = self.login()
        try:
            payload = self._call(
                "getTimetable",
                {"options": _timetable_options(session.identity, start, end)},
                session.session_id,
            )
        finally:
            self._logout()
        return parse_timetable(payload, start, end)

    def login(self) -> WebUntisSession:
        """Meldet an und liefert die Identität samt ``sessionId`` zurück.

        Der Aufrufer ist für ``logout`` zuständig, weil WebUntis die Sitzung
        serverseitig nach kurzer Leerlaufzeit räumt.
        """
        result = self._call(
            "authenticate",
            {"user": self._username, "password": self._password, "client": _CLIENT_ID},
        )
        return _session_from(result)

    def _logout(self) -> None:
        try:
            self._call("logout", {})
        except WebUntisError:
            pass

    def _call(self, method: str, params: dict[str, Any], session_id: str | None = None) -> Any:
        payload = {"id": 1, "method": method, "params": params, "jsonrpc": "2.0"}
        headers = dict(_JSONRPC_HEADERS)
        if session_id:
            headers["Cookie"] = f"JSESSIONID={session_id}"
        try:
            response = self._client.post(self._url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise WebUntisError("Zeitüberschreitung bei der Verbindung zum WebUntis-Server.") from exc
        except httpx.HTTPError as exc:
            raise WebUntisError(f"Der WebUntis-Server ist nicht erreichbar: {exc}") from exc

        body = _parse_body(response)
        if body is not None:
            error = body.get("error")
            if isinstance(error, dict):
                raise _error_from_payload(error.get("code"), str(error.get("message") or ""), self._school)
            result = body.get("result")
            if isinstance(result, dict) and result.get("code") is not None:
                raise _error_from_payload(result.get("code"), str(result.get("message") or ""), self._school)

        if response.status_code >= 500:
            raise WebUntisError(f"Der WebUntis-Server meldet einen Fehler (HTTP {response.status_code}).")
        if response.status_code >= 400:
            raise WebUntisConfigError(
                f"Die WebUntis-Adresse wurde abgelehnt (HTTP {response.status_code}). "
                "Bitte Schulname und Adresse prüfen."
            )
        if body is None:
            raise WebUntisError("Der WebUntis-Server hat eine nicht lesbare Antwort geliefert.")

        result = body.get("result")
        if result is None:
            raise WebUntisError("Der WebUntis-Server hat keine Nutzlast zurückgegeben.")
        return result


def _parse_body(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = json.loads(response.text)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def suggest_school_slug(school: str) -> str:
    """WebUntis erwartet den kurzen Schulkürzel-Namen, nicht die Anzeige.

    Aus "Lessing-Gymnasium Karlsruhe" wird so "lessinggymnasiumkarlsruhe".
    Das ist nur ein Vorschlag, die Autorität liegt beim Portal.
    """
    slug = "".join(character for character in school.lower() if character.isalnum())
    return slug


def _session_from(result: Any) -> WebUntisSession:
    if not isinstance(result, dict):
        raise WebUntisError("Der WebUntis-Server hat keine lesbare Sitzungsantwort geliefert.")
    session_id = result.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        raise WebUntisError("Der WebUntis-Server hat keine Sitzung zurückgegeben.")
    return WebUntisSession(
        session_id=session_id,
        identity=WebUntisIdentity(
            person_id=_as_int(result.get("personId")),
            person_type=_as_int(result.get("personType")),
            klasse_id=_as_int(result.get("klasseId")),
        ),
    )


def _timetable_options(identity: WebUntisIdentity, start: date, end: date) -> dict[str, Any]:
    """Baut das ``options``-Objekt für ``getTimetable``.

    WebUntis erwartet die Daten in ``YYYYMMDD`` ohne Trennzeichen und die
    ID des Elements, für das der Plan gelten soll. Die ``personType``-Nummer aus
    dem Login ist dieselbe Typnummer, die WebUntis für das Element erwartet;
    ein Konto ohne Personenbezug (0) wird als Schülerkonto behandelt.
    """
    element_id = identity.person_id or identity.klasse_id
    if element_id is None:
        raise WebUntisError(
            "WebUntis hat zu diesem Konto keine Person gemeldet, für die ein Stundenplan abgefragt werden könnte."
        )
    element_type = identity.person_type if identity.person_type else _ELEMENT_STUDENT
    if element_type not in _TIMETABLE_ELEMENT_TYPES:
        element_type = _ELEMENT_STUDENT
    return {
        "id": int(datetime.now(tz=UTC).timestamp() * 1000),
        "element": {"id": element_id, "type": element_type},
        "startDate": start.strftime("%Y%m%d"),
        "endDate": end.strftime("%Y%m%d"),
        **_TIMETABLE_SHOW_OPTIONS,
        "klasseFields": list(_ENTITY_FIELDS),
        "roomFields": list(_ENTITY_FIELDS),
        "subjectFields": list(_ENTITY_FIELDS),
        "teacherFields": list(_ENTITY_FIELDS),
    }


def parse_timetable(payload: Any, window_start: date, window_end: date) -> WebUntisTimetable:
    """Wandelt die ``getTimetable``-Antwort in Dataclasses um.

    Unvollständige Einträge werden übersprungen statt das Importieren ganzer
    Wochen abbrechen zu lassen; ein einzelner kaputter Eintrag darf den Rest
    nicht blockieren. Wie viele es waren, steht in ``skipped``.
    """
    if payload is None:
        return WebUntisTimetable(entries=(), window_start=window_start, window_end=window_end)
    if not isinstance(payload, list):
        raise WebUntisError("WebUntis hat den Stundenplan in einem unerwarteten Format geliefert.")

    entries: list[WebUntisLessonEntry] = []
    skipped = 0
    for raw in payload:
        parsed = _lesson_entry(raw)
        if parsed is None:
            skipped += 1
            continue
        entries.append(parsed)
    entries.sort(key=lambda entry: (entry.day, entry.start, entry.end))
    return WebUntisTimetable(
        entries=tuple(entries), window_start=window_start, window_end=window_end, skipped=skipped
    )


def _lesson_entry(raw: object) -> WebUntisLessonEntry | None:
    if not isinstance(raw, dict):
        return None
    day = _parse_day(raw.get("date"))
    start = _parse_clock(raw.get("startTime"))
    end = _parse_clock(raw.get("endTime"))
    if day is None or start is None or end is None or end <= start:
        return None
    identifier = raw.get("id")
    if not isinstance(identifier, int) or isinstance(identifier, bool):
        return None
    lesson_text = raw.get("lstext")
    return WebUntisLessonEntry(
        external_id=str(identifier),
        day=day,
        start=start,
        end=end,
        activity_type=_text(raw.get("activityType")) or "Unterricht",
        classes=_entities(raw.get("kl")),
        teachers=_entities(raw.get("te")),
        subjects=_entities(raw.get("su")),
        rooms=_entities(raw.get("ro")),
        lesson_text=_text(lesson_text) or None,
        is_cancelled=_text(raw.get("code")).lower() == CANCELLED_CODE,
    )


def _parse_day(value: object) -> date | None:
    """Liest ``20260925`` aus einer Zahl oder einer Ziffernfolge."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        digits = str(value)
    elif isinstance(value, str) and value.strip().isdigit():
        digits = value.strip()
    else:
        return None
    if len(digits) != 8:
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def _parse_clock(value: object) -> time | None:
    """Liest ``935`` oder ``935`` als 09:35 aus, WebUntis rechnet in HHMM."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        digits = f"{value:04d}"
    elif isinstance(value, str) and value.strip().isdigit():
        digits = value.strip().zfill(4)
    else:
        return None
    if len(digits) != 4:
        return None
    hours, minutes = int(digits[:2]), int(digits[2:])
    try:
        return time(hour=hours, minute=minutes)
    except ValueError:
        return None


def _entities(value: object) -> tuple[WebUntisEntity, ...]:
    if not isinstance(value, list):
        return ()
    found: list[WebUntisEntity] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        identifier = item.get("id")
        if not isinstance(identifier, int) or isinstance(identifier, bool):
            continue
        name = _text(item.get("name")) or str(identifier)
        found.append(WebUntisEntity(id=identifier, name=name, longname=_text(item.get("longname")) or None))
    return tuple(found)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _error_from_payload(code: object, message: str, school: str = "") -> WebUntisError:
    numeric = code if isinstance(code, int) and not isinstance(code, bool) else None
    if numeric in _AUTH_FAILED_CODES:
        return WebUntisAuthError("WebUntis hat Benutzername oder Passwort abgelehnt.")
    if numeric in _INVALID_SCHOOL_CODES:
        return WebUntisConfigError(_invalid_school_message(school))
    if numeric in _SESSION_CODES:
        return WebUntisError("Die WebUntis-Sitzung ist abgelaufen. Bitte erneut verbinden.")
    if numeric in _TIMEOUT_CODES:
        return WebUntisError("WebUntis hat die Anfrage wegen Zeitüberschreitung abgelehnt.")
    if numeric in _NO_SUCH_ELEMENT_CODES:
        return WebUntisConfigError(
            "WebUntis führt zu diesem Konto kein Element, zu dem ein Stundenplan gehört. "
            "Der Login ist gültig, aber das Konto ist vermutlich kein Schüler- oder Lehrerkonto."
        )
    detail = message.strip()
    if numeric is not None:
        return WebUntisError(f"WebUntis meldet Fehler {numeric}{f': {detail}' if detail else ''}.")
    return WebUntisError(detail or "WebUntis hat die Anfrage abgelehnt.")


def _invalid_school_message(school: str) -> str:
    suggestion = suggest_school_slug(school)
    hint = f" Versuche es mit '{suggestion}'." if suggestion and suggestion != school else ""
    return (
        f"WebUntis kennt die Schule '{school}' nicht.{hint} "
        "Das Kürzel steht meist in der Browser-Adresse des Schulportals."
    )


def probe_webuntis(
    base_url: str,
    school: str,
    username: str,
    password: str,
    timeout_seconds: float = 10.0,
) -> WebUntisIdentity:
    """Bequemlichkeit für Aufrufer, die nur die Verbindung prüfen wollen."""
    with WebUntisClient(base_url, school, username, password, timeout_seconds) as client:
        identity = client.check_connection()
    return identity
