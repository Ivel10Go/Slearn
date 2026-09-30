"""Lesen und Schreiben von iCalendar-Dateien (RFC 5545), ohne neue Abhängigkeit.

**Warum kein ``icalendar``/``ics`` im Projekt?** Weil die eine Entscheidung, an
der ein Kalender scheitern kann, hier sichtbar sein muss und nicht in einer
Bibliothek verschwinden darf: **wie eine Zeitangabe ohne Zeitzone gelesen
wird.** Eine ICS-Datei kann drei Sorten von ``DTSTART`` schreiben, und sie
bedeuten drei verschiedene Dinge:

``20260930T075000Z``
    UTC, durch das ``Z`` ausdrücklich. Eindeutig.

``DTSTART;TZID=Europe/Berlin:20260930T075000``
    Feste Zone, die in der Datei steht. Eindeutig, solange die Zone dem
    System bekannt ist; ``zoneinfo`` löst sie auf.

``DTSTART:20260930T075000``
    Eine *schwebende* Zeit. RFC 5545 sagt dazu ausdrücklich, sie sei "local to
    the attendee's timezone" - die Datei enthält also **keine** Information
    darüber, welche Zeit gemeint war. Es gibt keine richtige Antwort, nur eine
    getroffene. Diese wird in der Zeitzone des Nutzerkontos gelesen, wie der
    WebUntis-Import Schul-Ortszeit liest, und der Termin wird als
    ``floating`` markiert. Der Sync zählt diese Fälle separat und nennt die Zahl
    im Ergebnis, damit die Entscheidung sichtbar bleibt statt still zu
    geschehen. Ein Kalender, der Termine um zwei Stunden verschiebt, ist
   schlimmer als einer, der sich weigert; deshalb wird hier nichts geraten.

Nur die ``VEVENT``-Blöcke werden gelesen. ``VTODO``, ``VJOURNAL`` und
``VFREEBUSY`` gehören nicht zu einem Kalender, den diese App anzeigt.

**Wiederholungen werden nicht aufgelöst.** Ein ``VEVENT`` mit ``RRULE`` oder
``RDATE`` trägt nur seinen *ersten* Termin; die Folgetermine stecken in der
Regel. Wer sie flach übernähme, hätte eine Liste voller falscher Daten.
Deshalb werden solche Einträge übersprungen und gezählt - der Sync nennt die
Zahl. Umgekehrt schreibt der Export **keine** ``RRULE``: ein von hier
ausgehender Termin ist ein einzelner Termin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PRODID: Final = "-//school-assistant//Lernraum Kalender//DE"
_LINE_BREAK: Final = "\r\n"
# Zeilen werden nach 75 Oktetten gefaltet, weil RFC 5545 das so vorsieht. Die
# Grenze zählt Oktette und nicht Zeichen: ein Umlaut belegt zwei.
_OCTET_LIMIT: Final = 75
_DATETIME_PATTERN: Final = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z)?$")
_DATE_PATTERN: Final = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_DURATION_PATTERN: Final = re.compile(
    r"^(?P<sign>[+-])?P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)
_COMPONENT_NAME: Final = re.compile(r"^(?P<art>BEGIN|END):(?P<name>[A-Z-]+)$", re.IGNORECASE)
_DEFAULT_TIMED_LENGTH: Final = timedelta(hours=1)
_DEFAULT_ALL_DAY_LENGTH: Final = timedelta(days=1)


class ICalError(ValueError):
    """Der Text ist kein iCalendar-Kalender, oder ein Termin darin ist unbrauchbar."""


@dataclass(frozen=True, slots=True)
class ICalEvent:
    """Ein gelesener Termin.

    ``start``/``end`` sind **immer zeitzonenbewusst**. Ob die Zone aus der Datei
    stammte oder aus dem Nutzerkonto, steht in ``floating``: ein schwebender
    Termin ist gültig, aber er wurde geraten.
    """

    uid: str
    summary: str
    start: datetime
    end: datetime
    description: str | None = None
    all_day: bool = False
    floating: bool = False
    status: str | None = None
    sequence: int | None = None
    modified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ICalCalendar:
    """Ein gelesener Kalender samt der Zahl bewusst übersprungener Einträge."""

    events: tuple[ICalEvent, ...]
    skipped: int = 0
    #: Der Kalendername aus ``X-WR-CALNAME``, falls vorhanden.
    name: str | None = None
    #: Warum übersprungene Einträge nicht lesbar waren, ohne Wiederholungen.
    reasons: tuple[str, ...] = ()


def parse_calendar(text: str, default_zone: ZoneInfo) -> ICalCalendar:
    """Liest alle ``VEVENT``-Blöcke eines iCalendar-Textes.

    ``default_zone`` gilt für schwebende Zeiten ohne ``TZID`` - siehe den
    Modul-Docstring. Ein Termin ohne ``DTEND`` und ohne ``DURATION`` bekommt
    eine Stunde (ganztägig einen Tag); eine kaputte Dauer macht den Termin
    unbrauchbar statt still auf eine Stunde zu setzen.
    """
    lines = _unfold(text)
    events: list[ICalEvent] = []
    reasons: list[str] = []
    kalendername: str | None = None
    saw_calendar = False

    # Ein Stapel statt eines Flags: ein ``VEVENT`` liegt **im** ``VCALENDAR``,
    # und seine Zeilen gehören ihm, nicht der Hülle. Mit einem einfachen
    # "letztes BEGIN gewinnt"-Zustand fielen alle Termine in den Kalenderblock
    # und würden nie ausgewertet.
    stack: list[str] = []
    props: list[list[tuple[str, dict[str, str], str]]] = []

    for line in lines:
        komponente = _component(line)
        if komponente is not None:
            art, name = komponente
            if art == "BEGIN":
                stack.append(name)
                props.append([])
                continue
            if not stack:
                continue
            geschlossen = stack.pop()
            eigene = props.pop() if props else []
            if geschlossen == "VEVENT":
                termin, grund = _parse_event(eigene, default_zone)
                if termin is None:
                    reasons.append(grund or "unlesbarer Eintrag")
                else:
                    events.append(termin)
            elif geschlossen == "VCALENDAR":
                saw_calendar = True
                for eigenschaft, _params, wert in eigene:
                    if eigenschaft == "X-WR-CALNAME":
                        kalendername = _unescape(wert) or None
            continue
        if props:
            zeile = _split_property(line.strip())
            if zeile is not None:
                props[-1].append(zeile)

    if not saw_calendar:
        raise ICalError("Der Text beginnt nicht mit einem iCalendar-Kalender.")

    return ICalCalendar(
        events=tuple(events),
        skipped=len(reasons),
        name=kalendername,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def _component(line: str) -> tuple[str, str] | None:
    """``BEGIN:VEVENT`` zu ``("BEGIN", "VEVENT")``, sonst ``None``."""
    match = _COMPONENT_NAME.match(line.strip())
    if match is None:
        return None
    return match.group("art").upper(), match.group("name").upper()


def build_calendar(events: list[ICalEvent], calendar_name: str, timezone_name: str) -> str:
    """Schreibt Termine als iCalendar-Text, der wieder verlustfrei lesbar ist.

    Zeitpunkte gehen **immer** als UTC mit ``Z`` hinaus, ganztägige Termine als
    ``VALUE=DATE`` mit exklusivem ``DTEND`` (das ist die iCalendar-Regel: ein
    ganztägiges ``DTEND`` nennt den Tag *nach* dem Termin). Damit steht in der
    Datei bei jedem Termin eine Zone, und der Weg zurück braucht keine Annahme.
    """
    out: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(calendar_name)}",
        f"X-WR-TIMEZONE:{_escape(timezone_name)}",
    ]
    for event in events:
        out.extend(_render_event(event))
    out.append("END:VCALENDAR")
    return _LINE_BREAK.join(fold(line) for line in out) + _LINE_BREAK


def _render_event(event: ICalEvent) -> list[str]:
    stamp = (event.modified_at or datetime.now(UTC)).astimezone(UTC)
    lines = [
        "BEGIN:VEVENT",
        f"UID:{_escape(event.uid)}",
        f"DTSTAMP:{_format_utc(stamp)}",
    ]
    if event.all_day:
        lines.append(f"DTSTART;VALUE=DATE:{event.start.date().strftime('%Y%m%d')}")
        lines.append(f"DTEND;VALUE=DATE:{event.end.date().strftime('%Y%m%d')}")
    else:
        lines.append(f"DTSTART:{_format_utc(event.start)}")
        lines.append(f"DTEND:{_format_utc(event.end)}")
    lines.append(f"SUMMARY:{_escape(event.summary)}")
    if event.description:
        lines.append(f"DESCRIPTION:{_escape(event.description)}")
    if event.sequence is not None:
        lines.append(f"SEQUENCE:{event.sequence}")
    if event.modified_at is not None:
        lines.append(f"LAST-MODIFIED:{_format_utc(stamp)}")
    if event.status:
        lines.append(f"STATUS:{event.status.upper()}")
    lines.append("END:VEVENT")
    return lines


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _unfold(text: str) -> list[str]:
    """Macht aus gefalteten Zeilen wieder logische Zeilen.

    RFC 5545 faltet eine lange Zeile, indem er an einer beliebigen Stelle ein
    CRLF einfügt, das mit einem Leerzeichen oder Tabulator beginnt.
    """
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    logical: list[str] = []
    for line in raw:
        if line[:1] in {" ", "\t"} and logical:
            logical[-1] += line[1:]
        else:
            logical.append(line)
    return [line for line in logical if line.strip()]


def _split_property(line: str) -> tuple[str, dict[str, str], str] | None:
    if ":" not in line:
        return None
    head, _, value = line.partition(":")
    pieces = head.split(";")
    name = pieces[0].strip().upper()
    if not name:
        return None
    params: dict[str, str] = {}
    for piece in pieces[1:]:
        key, _, param_value = piece.partition("=")
        params[key.strip().upper()] = param_value.strip().strip('"')
    return name, params, value


def _parse_event(
    properties: list[tuple[str, dict[str, str], str]],
    default_zone: ZoneInfo,
) -> tuple[ICalEvent | None, str | None]:
    values: dict[str, tuple[dict[str, str], str]] = {}
    recurrence = False
    for name, params, value in properties:
        if name in {"RRULE", "RDATE", "EXDATE", "RECURRENCE-ID"}:
            recurrence = True
            continue
        values.setdefault(name, (params, value))

    if recurrence:
        return None, "Wiederholungen werden nicht aufgelöst und deshalb übersprungen"
    # `UID:` mit nichts dahinter ist dasselbe wie kein UID: ohne Kennung kann der
    # Termin beim nächsten Abruf nicht wiedergefunden werden.
    uid = _unescape(values["UID"][1]).strip() if "UID" in values else ""
    if not uid:
        return None, "Ein Termin ohne UID kann nicht wiedergefunden werden"
    if "DTSTART" not in values:
        return None, "Ein Termin ohne DTSTART ist kein Termin"

    params, raw_start = values["DTSTART"]
    try:
        start, all_day = _parse_temporal(raw_start, params, default_zone)
        floating = _is_floating(params, raw_start)
        end = _resolve_end(values, start, all_day, default_zone)
    except ICalError as exc:
        return None, str(exc)

    description = _unescape(values["DESCRIPTION"][1]) if "DESCRIPTION" in values else None
    summary = _unescape(values["SUMMARY"][1]) if "SUMMARY" in values else "(ohne Titel)"
    return (
        ICalEvent(
            uid=uid,
            summary=summary.strip() or "(ohne Titel)",
            start=start,
            end=end,
            description=description,
            all_day=all_day,
            floating=floating,
            status=_unescape(values["STATUS"][1]).strip().upper() if "STATUS" in values else None,
            sequence=_parse_int(values["SEQUENCE"][1]) if "SEQUENCE" in values else None,
            modified_at=_parse_optional_utc(values["LAST-MODIFIED"][1]) if "LAST-MODIFIED" in values else None,
        ),
        None,
    )


def _resolve_end(
    values: dict[str, tuple[dict[str, str], str]],
    start: datetime,
    all_day: bool,
    default_zone: ZoneInfo,
) -> datetime:
    if "DTEND" in values:
        params, raw_end = values["DTEND"]
        end, _ = _parse_temporal(raw_end, params, default_zone)
        return end
    if "DURATION" in values:
        return start + _parse_duration(_unescape(values["DURATION"][1]))
    return start + (_DEFAULT_ALL_DAY_LENGTH if all_day else _DEFAULT_TIMED_LENGTH)


def _parse_temporal(raw: str, params: dict[str, str], default_zone: ZoneInfo) -> tuple[datetime, bool]:
    value = raw.strip()
    match = _DATETIME_PATTERN.match(value)
    if match is not None:
        year, month, day, hour, minute, second, zulu = match.groups()
        naive = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second))
        if zulu is not None:
            return naive.replace(tzinfo=UTC), False
        return naive.replace(tzinfo=_zone_from_params(params, default_zone)), False
    match = _DATE_PATTERN.match(value)
    if match is not None:
        year, month, day = match.groups()
        zone = _zone_from_params(params, default_zone)
        return datetime.combine(date(int(year), int(month), int(day)), datetime.min.time(), tzinfo=zone), True
    raise ICalError(f"Die Zeitangabe '{value}' ist unlesbar")


def _zone_from_params(params: dict[str, str], default_zone: ZoneInfo) -> ZoneInfo:
    tzid = params.get("TZID", "").strip()
    if not tzid:
        return default_zone
    # Manche Kalender schreiben "W. Europe Standard Time" oder ähnliche
    # Microsoft-Bezeichner. Für solche Fälle gilt dieselbe Annahme wie für eine
    # schwebende Zeit: die Zeitzone des Nutzerkontos.
    if tzid.lower() in {"utc", "z"}:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(tzid)
    except (ZoneInfoNotFoundError, ValueError):
        return default_zone


def _is_floating(params: dict[str, str], raw: str) -> bool:
    """Trifft die Aussage "die Datei nannte keine Zone"."""
    if raw.strip().endswith("Z"):
        return False
    return not params.get("TZID", "").strip()


def _parse_duration(raw: str) -> timedelta:
    match = _DURATION_PATTERN.match(raw.strip())
    if match is None or match.group(0).strip() in {"P", "PT", ""}:
        raise ICalError(f"Die Dauer '{raw}' ist unlesbar")
    groups = match.groupdict()
    delta = timedelta(
        days=int(groups.get("days") or 0),
        hours=int(groups.get("hours") or 0),
        minutes=int(groups.get("minutes") or 0),
        seconds=int(groups.get("seconds") or 0),
    )
    return -delta if groups.get("sign") == "-" else delta


def _parse_optional_utc(raw: str) -> datetime | None:
    value = raw.strip()
    match = _DATETIME_PATTERN.match(value)
    if match is None:
        return None
    year, month, day, hour, minute, second, zulu = match.groups()
    naive = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second))
    return naive.replace(tzinfo=UTC) if zulu is not None else None


def _parse_int(raw: str) -> int | None:
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _unescape(value: str) -> str:
    out: list[str] = []
    escaped = False
    for char in value:
        if escaped:
            out.append({"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}.get(char, char))
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            out.append(char)
    return "".join(out)


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\n")
        .replace("\n", "\\n")
    )


def fold(line: str) -> str:
    """Faltet eine Zeile nach 75 Oktetten, ohne ein Zeichen zu zerschneiden."""
    if len(line.encode("utf-8")) <= _OCTET_LIMIT:
        return line
    out: list[str] = []
    current: list[str] = []
    size = 0
    for char in line:
        char_size = len(char.encode("utf-8"))
        # Ein einzelnes Zeichen passt immer; `_OCTET_LIMIT - 1` lässt Platz für
        # das Leerzeichen, das die Fortsetzung einleitet.
        if size + char_size > _OCTET_LIMIT - 1:
            out.append("".join(current))
            current = []
            size = 0
        current.append(char)
        size += char_size
    out.append("".join(current))
    return _LINE_BREAK.join([out[0], *(f" {part}" for part in out[1:])])
