"""Der Abgleich zwischen einem Kalender in der App und einer Gegenseite.

Dieses Modul enthält die **Regel**, nicht das Protokoll. Wie Google oder ein
CalDAV-Server antworten, steht in ``google_calendar.py`` und ``caldav.py``; wie
ein iCalendar-Abo ausgeliefert wird, in ``IcalAdapter`` unten. Die Regel steht
genau einmal hier, damit es nicht drei Kopien gibt, die auseinanderlaufen.

Die Regel in einem Satz
-----------------------

**Es gewinnt die Änderung, die älter ist, nicht die, die später kommt.** Genauer:
wurde in der App etwas geändert, holt ein Abruf es nicht zurück, und es wird
nachgeschoben; wurde in der Gegenseite etwas geändert, holt der Abruf es und
schreibt nicht zurück. Stehen beide Änderungen nebeneinander, **gewinnt die
lokal gemachte**, die fremde wird gezählt und gemeldet statt stillschweigend
verworfen.

Warum lokal und nicht "der Abruf ist der Wahrheitsgeber"
--------------------------------------------------------

Der WebUntis-Import darf überschreiben, weil WebUntis die Quelle der Wahrheit
ist: dort entsteht der Stundenplan. Für einen Kalender stimmt das nicht. Wer
einen Termin in der App verschiebt, schreibt das in dieselbe Datenbank, aus der
der Abruf liest. Ein naives Überschreiben löscht diese Änderung beim nächsten
Knopfdruck - und zwar still. Deshalb steht der Zustand in der Zeile
(``calendar_events.sync_state``) und nicht in einem Versuch, beides zu erkennen.

Der Ablauf eines Laufs, in dieser Reihenfolge
---------------------------------------------

1. **Lesen.** Alle Gegentermine im Fenster holen.
2. **Übernahme.** Pro Termin:

   * unbekannt → neu anlegen, ``synced``.
   * ``synced`` und das ``etag`` ist unverändert → nichts tun.
   * ``synced`` und das ``etag`` hat sich geändert → **fremde Änderung**, Felder
     übernehmen, neues ``etag`` speichern.
   * ``edited`` → **lokale Änderung schlägt**. Nichts übernehmen, ``etag``
     bewusst *nicht* aktualisieren: der Schreibvorgang vergleicht später mit
     genau der Fassung, auf der die lokale Änderung beruhte. Stimmt die
     Gegenseite nicht mehr damit überein, meldet der Schreibvorgang einen
     Konflikt, statt etwas zu überschreiben.
   * ``deleted`` → Grabstein. Der Abruf fasst nichts an; der Löschvorgang unten
     erledigt ihn.

3. **Verschwundene Gegentermine.** Was im Fenster früher war und jetzt nicht mehr:

   * ``synced`` → in der Gegenseite gelöscht, Zeile löschen.
   * ``edited`` → **lokal gewinnt**: die Zeile bleibt, und ``remote_etag`` wird
     geleert. Der Schreibvorgang legt den Termin dann neu an, statt mit einem
     ``If-Match`` zu arbeiten, das garantiert fehlschlägt.
   * ``deleted`` → bleibt als Grabstein, siehe oben.

4. **Nachschieben.** Für ``edited`` und ``deleted``, nur wenn die Gegenseite
   Schreiben unterstützt **und** die Einwilligung erteilt ist:

   * ``edited`` → ``PUT`` mit ``If-Match``, wenn ein ``etag`` bekannt ist,
     sonst bedingungslos anlegen. 412 heißt: die Gegenseite hat zwischenzeitlich
     etwas geändert. Das ist ein **Konflikt**, kein Fehler - der Zustand bleibt
     ``edited``, das ``etag`` bleibt stehen, und der Lauf zählt und meldet es.
   * ``deleted`` → ``DELETE`` mit ``If-Match``, falls bekannt. 404 heißt
     "gibt es dort nicht mehr" und ist damit ein Erfolg. Danach wird die Zeile
     gelöscht.

Zwei Dinge, die bewusst **nicht** passieren
------------------------------------------

* **Termine ohne ``sync_source_id`` werden nie angefasst.** Von Hand angelegte
  Termine gehören niemandem außer der Lernenden - dieselbe Replace-Semantik wie
  beim WebUntis-Import, nur schärfer: dort wird das Zeitfenster ersetzt, hier
  bleibt alles außerhalb der Quelle unangetastet.
* **Ein Konflikt bricht den Lauf nicht ab.** Die restlichen Termine werden
  verarbeitet und der Lauf meldet am Ende, wie viele offen blieben. Ein Abruf,
  der beim ersten widersprüchlichen Termin abbricht, wäre schlechter als einer,
  der vier von fünf schafft und den fünften meldet.

Fenster und Wiederholungen
-------------------------

Das Fenster ist dasselbe wie beim WebUntis-Abruf - Montag der aktuellen Woche bis
vier Wochen später (``sync_window``) - und wird hier **lokal** gerechnet: ein
Kalender liefert Termine als Zeitpunkte, und ein Vergleich in der Zeitzone des
Nutzers ist der, den er auch in der Oberfläche sieht.

Wiederholungen (``RRULE``) werden **nicht** aufgelöst, siehe ``ical.py``. Sie zu
übernehmen hieße, den ersten Termin zu kopieren und alle folgenden zu verlieren -
eine Liste, die genau einmal richtig ist.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Final, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import as_utc, utc_now
from app.db.entities import (
    SYNC_STATE_DELETED,
    SYNC_STATE_EDITED,
    SYNC_STATE_SYNCED,
    CalendarEvent,
    SyncSource,
)
from app.services.ical import ICalCalendar, ICalError, ICalEvent, parse_calendar

logger = logging.getLogger("school_assistant.calendar_sync")

#: Wie groß eine ICS-Quelle sein darf. Eine abonnierte Adresse liefert eine
#: Kalenderdatei; eine Datei, die keine ist, gehört abgelehnt - auf einem Server
#: ohne Speichergrenze wäre ein 2-Gigabyte-Text ein Leichtgewicht.
MAX_ICAL_BYTES: Final = 16 * 1024 * 1024


class CalendarSyncError(RuntimeError):
    """Basis für alle Fehler aus dem Abgleich mit einer Gegenseite."""


class CalendarConfigError(CalendarSyncError):
    """Die hinterlegte Konfiguration ist unbrauchbar (falsche URL, fehlende Datei)."""


class CalendarAuthError(CalendarSyncError):
    """Die Gegenseite hat die Anmeldedaten oder das Token abgelehnt."""


class CalendarConflictError(CalendarSyncError):
    """Die Gegenseite hat den Termin seit dem letzten Abruf selbst geändert."""


class CalendarNotWritableError(CalendarSyncError):
    """Diese Quelle kann nicht beschrieben werden (ein iCalendar-Abo ist read-only)."""


@dataclass(frozen=True, slots=True)
class RemoteEvent:
    """Ein Termin, wie ihn die Gegenseite liefert.

    ``etag`` ist die Fassung, die die Gegenseite gerade für sich hält. Es ist
    nicht Teil des Termins, sondern seines Zustands - deshalb steht es hier und
    nicht im Kalender.
    """

    uid: str
    title: str
    start: datetime
    end: datetime
    description: str | None = None
    all_day: bool = False
    #: True, wenn die Quelle keine Zeitzone nannte; siehe ``ical.py``.
    floating: bool = False
    etag: str | None = None
    modified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RemoteBatch:
    """Das Ergebnis eines Lesens: Termine plus die Zahl unlesbarer Einträge.

    Die Zahl steht hier und nicht in einer Ausnahme, weil ein Kalender ganz
    normale Einträge enthält, die man nicht übernehmen kann (etwa
    Wiederholungen). Ein Abruf, der bei einem solchen Eintrag abbricht, holt
    gar nichts - also wird gezählt und gemeldet.
    """

    events: tuple[RemoteEvent, ...] = ()
    skipped: int = 0


class CalendarAdapter(Protocol):
    """Was der Abgleich von einem Adapter braucht - mehr nicht.

    ``supports_writing`` steht als Attribut und nicht als Ausnahme: eine Quelle,
    die nicht schreiben kann, soll beim Schreibvorgang gar nicht erst versucht
    werden. Die Einwilligung ist eine **zusätzliche** Bedingung, keine
    Alternative.
    """

    kind: str
    supports_writing: bool

    def check_connection(self) -> str:
        """Echter Verbindungstest, gibt einen deutschen Satz zurück."""

    def fetch_events(self, start: datetime, end: datetime) -> RemoteBatch:
        """Alle Termine, die das Fenster berühren."""

    def push_event(self, event: RemoteEvent, etag: str | None) -> RemoteEvent:
        """Legt an oder ersetzt einen Termin; ``etag`` ist das ``If-Match``, falls bekannt."""

    def delete_event(self, uid: str, etag: str | None) -> None:
        """Löscht einen Termin; ein 404 der Gegenseite gilt als erledigt."""


@dataclass(frozen=True, slots=True)
class CalendarSyncOutcome:
    """Was ein Lauf geliefert hat, in Zahlen und in einem deutschen Satz."""

    imported_events: int
    updated_events: int
    pushed_events: int
    deleted_events: int
    removed_events: int
    conflicts: int
    skipped_entries: int
    floating_events: int
    window_start: date
    window_end: date

    def describe(self) -> str:
        parts = [
            f"{self.imported_events} Termine neu importiert",
            f"{self.updated_events} aus der Quelle übernommen",
            f"{self.removed_events} gelöscht",
        ]
        if self.pushed_events:
            parts.append(f"{self.pushed_events} nach oben geschoben")
        if self.deleted_events:
            parts.append(f"{self.deleted_events} dort gelöscht")
        sentence = (
            ", ".join(parts)
            + f" ({self.window_start.strftime('%d.%m.%Y')} bis {self.window_end.strftime('%d.%m.%Y')})."
        )
        if self.floating_events:
            sentence += (
                f" {self.floating_events} Termine nannten keine Zeitzone und wurden in deiner Zeitzone gelesen."
            )
        if self.skipped_entries:
            sentence += f" {self.skipped_entries} Einträge waren unlesbar oder wiederholt und wurden übersprungen."
        if self.conflicts:
            sentence += f" {self.conflicts} Termine wurden an beiden Seiten geändert und sind offen geblieben."
        return sentence


def zone_of(name: str) -> ZoneInfo:
    """Die Zeitzone des Namens; eine kaputte Zone darf den Lauf nicht stoppen."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def window_bounds(window_start: date, window_end: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """Rechnet ein Tagesfenster in der Nutzerzone in UTC um.

    Das Fenster beginnt in der lokalen Mitternacht des ersten Tages und endet in
    der lokalen Mitternacht **nach** dem letzten Tag. Deshalb ein zusätzlicher
    Tag: ein ganztägiger Termin am letzten Tag endet in der Datenbank in der
    Nacht, und ein auf ``end`` geschlossenes Fenster liefe an ihm vorbei.
    """
    start = as_utc(datetime.combine(window_start, time.min, tzinfo=zone))
    end = as_utc(datetime.combine(window_end + timedelta(days=1), time.min, tzinfo=zone))
    return start, end


def external_key(source: SyncSource, uid: str) -> str:
    """Bildet den Schlüssel ab, unter dem ein Termin wiedergefunden wird.

    Die Id der Quelle vor dem ``uid`` ist **nicht** Kosmetik: die eindeutige
    Kombination lautet ``(user_id, source, external_id)``, und ``source`` ist nur
    die Art (``"ical"``). Zwei Abo-Adressen mit derselben UID - durchaus
    möglich, beide aus derselben Schule - würden sich sonst gegenseitig
    überschreiben, und die Eindeutigkeit wäre eine Lüge.
    """
    return f"{source.id}:{uid}"


def to_remote(row: CalendarEvent, source: SyncSource) -> RemoteEvent:
    """Baut aus einer Zeile der Datenbank das, was ein Adapter schreibt."""
    uid = uid_of(row, source)
    return RemoteEvent(
        uid=uid if uid is not None else f"lernraum-{row.id}",
        title=row.title,
        start=as_utc(row.start_time),
        end=as_utc(row.end_time),
        description=row.description,
        all_day=row.all_day,
    )


def uid_of(row: CalendarEvent, source: SyncSource) -> str | None:
    """Die ``uid`` der Gegenseite aus ``external_id``, ohne den Quellen-Präfix."""
    if row.external_id is None:
        return None
    prefix = f"{source.id}:"
    return row.external_id[len(prefix) :] if row.external_id.startswith(prefix) else row.external_id


def sync_calendar(
    db: Session,
    source: SyncSource,
    adapter: CalendarAdapter,
    *,
    window_start: date,
    window_end: date,
    timezone_name: str,
    allow_writing: bool,
) -> CalendarSyncOutcome:
    """Holt, entscheidet und schiebt nach - einmal für alle Gegenseiten."""
    if not source.enabled:
        raise CalendarConfigError("Die Integration ist deaktiviert.")

    zone = zone_of(timezone_name)
    scope_start, scope_end = window_bounds(window_start, window_end, zone)
    batch = adapter.fetch_events(scope_start, scope_end)
    by_key = {external_key(source, event.uid): event for event in batch.events}

    existing = _existing_rows(db, source)

    imported = updated = floating = 0
    for key, remote in by_key.items():
        row = existing.get(key)
        if row is None:
            _insert(db, source, key, remote)
            imported += 1
            floating += int(remote.floating)
            continue
        if row.sync_state != SYNC_STATE_SYNCED:
            # `edited` und `deleted` gehören der Lernenden. Nichts übernehmen -
            # und das `etag` bewusst stehen lassen, damit der Schreibvorgang
            # später eine Abweichung erkennen kann.
            continue
        if remote.etag is not None and remote.etag == row.remote_etag:
            continue
        _overwrite(row, remote)
        updated += 1
        floating += int(remote.floating)

    removed = _drop_vanished(db, existing, set(by_key))

    conflicts = 0
    pushed = deleted = 0
    if allow_writing and adapter.supports_writing:
        pushed, deleted, conflicts = _push_pending(db, source, adapter)
    else:
        # Ohne Schreiben bleiben die offenen Zeilen liegen. Sie zu zählen ist
        # ehrlicher als zu schweigen: sonst wüsste niemand, dass in der App
        # etwas wartet, das nie ankommt.
        conflicts = sum(1 for row in existing.values() if row.sync_state in {SYNC_STATE_EDITED, SYNC_STATE_DELETED})

    source.last_sync_at = utc_now()
    source.status = "connected"
    source.last_error = None
    db.commit()

    return CalendarSyncOutcome(
        imported_events=imported,
        updated_events=updated,
        pushed_events=pushed,
        deleted_events=deleted,
        removed_events=removed,
        conflicts=conflicts,
        skipped_entries=batch.skipped,
        floating_events=floating,
        window_start=window_start,
        window_end=window_end,
    )


def _existing_rows(db: Session, source: SyncSource) -> dict[str, CalendarEvent]:
    rows = db.scalars(
        select(CalendarEvent).where(
            CalendarEvent.user_id == source.user_id,
            CalendarEvent.sync_source_id == source.id,
        )
    ).all()
    return {row.external_id: row for row in rows if row.external_id is not None}


def _insert(db: Session, source: SyncSource, key: str, remote: RemoteEvent) -> None:
    db.add(
        CalendarEvent(
            user_id=source.user_id,
            sync_source_id=source.id,
            title=remote.title[:240],
            description=remote.description,
            start_time=as_utc(remote.start),
            end_time=as_utc(remote.end),
            all_day=remote.all_day,
            source=source.source_type,
            external_id=key,
            sync_state=SYNC_STATE_SYNCED,
            remote_etag=remote.etag,
            remote_modified_at=remote.modified_at,
        )
    )


def _overwrite(row: CalendarEvent, remote: RemoteEvent) -> None:
    row.title = remote.title[:240]
    row.description = remote.description
    row.start_time = as_utc(remote.start)
    row.end_time = as_utc(remote.end)
    row.all_day = remote.all_day
    row.remote_etag = remote.etag
    row.remote_modified_at = remote.modified_at


def _drop_vanished(db: Session, existing: dict[str, CalendarEvent], seen: set[str]) -> int:
    """Räumt auf, was in der Gegenseite nicht mehr da ist."""
    vanished = [row for key, row in existing.items() if key not in seen]
    if not vanished:
        return 0
    # Die Zahl wird **vor** dem Löschen gebildet: nach ``flush`` ist eine
    # gelöschte Zeile aus der Sitzung verschwunden und ihre Felder nicht mehr
    # lesbar.
    removed = sum(1 for row in vanished if row.sync_state != SYNC_STATE_DELETED)
    for row in vanished:
        if row.sync_state == SYNC_STATE_SYNCED:
            db.delete(row)
        elif row.sync_state == SYNC_STATE_EDITED:
            # Der Termin ist lokal noch da, remote existiert er nicht mehr. Das
            # `etag` leeren, damit der Schreibvorgang neu anlegt statt mit einem
            # `If-Match` zu arbeiten, das immer fehlschlagen würde.
            row.remote_etag = None
            row.remote_modified_at = None
        # `deleted` bleibt: der Grabstein erledigt sich beim Schreibvorgang.
    db.flush()
    return removed


def _push_pending(db: Session, source: SyncSource, adapter: CalendarAdapter) -> tuple[int, int, int]:
    """Schiebt lokale Änderungen zur Gegenseite nach.

    Ein ``CalendarAuthError`` wird **nicht** geschluckt: ein abgelehntes Token
    betrifft alle folgenden Termine genauso, und ein Lauf, der danach noch
    weiterarbeitet, würde nur mehrfach dasselbe melden. Der Aufrufer schreibt
    den Grund in ``last_error``.
    """
    rows = db.scalars(
        select(CalendarEvent)
        .where(
            CalendarEvent.user_id == source.user_id,
            CalendarEvent.sync_source_id == source.id,
            CalendarEvent.sync_state.in_([SYNC_STATE_EDITED, SYNC_STATE_DELETED]),
        )
        .order_by(CalendarEvent.id)
    ).all()

    pushed = deleted = conflicts = 0
    for row in rows:
        uid = uid_of(row, source)
        if uid is None:
            logger.warning("Kalendertermin %s hat keine externe ID und wird nicht geschoben.", row.id)
            continue
        try:
            if row.sync_state == SYNC_STATE_DELETED:
                adapter.delete_event(uid, row.remote_etag)
                db.delete(row)
                deleted += 1
                continue
            stored = adapter.push_event(to_remote(row, source), row.remote_etag)
        except CalendarConflictError:
            # Die Gegenseite hat zwischenzeitlich etwas geändert. Nichts wird
            # überschrieben und nichts geht verloren: `edited` bleibt, das `etag`
            # bleibt, und der Lauf zählt.
            conflicts += 1
            continue
        row.sync_state = SYNC_STATE_SYNCED
        row.remote_etag = stored.etag
        row.remote_modified_at = stored.modified_at
        pushed += 1
    db.flush()
    return pushed, deleted, conflicts


class IcalAdapter:
    """Liest eine ICS-Adresse oder eine ICS-Datei aus dem Ablageordner.

    Read-only, und zwar nicht aus Nachlässigkeit: ein abonnierter Kalender gehört
    jemand anderem. Ein ``PUT`` dorthin ginge ins Leere oder, schlimmer, in eine
    Quelle, die gerade von jemand anderem beschrieben wird. Deshalb steht
    ``supports_writing`` auf ``False``, und deshalb ist der Schreibpfad für diese
    Quelle unabhängig von der Einwilligung tot - Abonnement *und* Einwilligung
    müssen beide vorliegen.
    """

    kind = "ical"
    supports_writing = False

    def __init__(self, *, url: str, path: str, storage_root: str, timezone_name: str, timeout_seconds: float) -> None:
        self._url = url.strip()
        self._path = path.strip()
        self._storage_root = storage_root
        self._zone = zone_of(timezone_name)
        self._timeout = timeout_seconds

    def check_connection(self) -> str:
        calendar = self._read()
        if not calendar.events and calendar.skipped == 0:
            return "Die Quelle ist erreichbar, enthält aber keine lesbaren Termine."
        text = f"Die Quelle ist erreichbar und enthält {len(calendar.events)} lesbare Termine."
        if calendar.skipped:
            text += f" {calendar.skipped} Einträge wurden übersprungen."
        return text

    def fetch_events(self, start: datetime, end: datetime) -> RemoteBatch:
        calendar = self._read()
        return RemoteBatch(
            events=tuple(
                _from_ical(event)
                for event in calendar.events
                if _overlaps(event.start, event.end, start, end)
            ),
            skipped=calendar.skipped,
        )

    def push_event(self, event: RemoteEvent, etag: str | None) -> RemoteEvent:
        raise CalendarNotWritableError("Eine abonnierte ICS-Quelle lässt sich nicht beschreiben.")

    def delete_event(self, uid: str, etag: str | None) -> None:
        raise CalendarNotWritableError("Eine abonnierte ICS-Quelle lässt sich nicht beschreiben.")

    def _read(self) -> ICalCalendar:
        try:
            return parse_calendar(self._fetch_text(), self._zone)
        except ICalError as exc:
            raise CalendarConfigError(f"Die ICS-Quelle ist unlesbar: {exc}") from exc

    def _fetch_text(self) -> str:
        if self._url:
            return self._read_url()
        return self._read_file()

    def _read_url(self) -> str:
        try:
            with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
                response = client.get(self._url, headers={"Accept": "text/calendar"})
        except httpx.TimeoutException as exc:
            raise CalendarSyncError("Zeitüberschreitung beim Abrufen der ICS-Quelle.") from exc
        except httpx.HTTPError as exc:
            raise CalendarSyncError(f"Die ICS-Quelle ist nicht erreichbar: {exc}") from exc
        if response.status_code >= 400:
            raise CalendarSyncError(f"Die ICS-Quelle hat die Anfrage abgelehnt (HTTP {response.status_code}).")
        return _guard_size(response.text, f"{len(response.content)} Bytes")

    def _read_file(self) -> str:
        root = Path(self._storage_root).resolve()
        candidate = Path(self._path)
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if not resolved.is_relative_to(root):
            raise CalendarConfigError("Der ICS-Pfad liegt außerhalb des konfigurierten Ablageordners.")
        if not resolved.is_file():
            raise CalendarConfigError(f"Die ICS-Datei {resolved.name} wurde nicht gefunden.")
        try:
            return _guard_size(resolved.read_text(encoding="utf-8"), f"{resolved.stat().st_size} Bytes")
        except OSError as exc:
            raise CalendarConfigError(f"Die ICS-Datei ließ sich nicht lesen: {exc.strerror}.") from exc


def _from_ical(event: ICalEvent) -> RemoteEvent:
    """Übersetzt einen gelesenen Termin in die Form, mit der der Abgleich rechnet."""
    return RemoteEvent(
        uid=event.uid,
        title=event.summary,
        start=event.start,
        end=event.end,
        description=event.description,
        all_day=event.all_day,
        floating=event.floating,
        etag=_ics_version(event.sequence, event.modified_at),
        modified_at=event.modified_at,
    )


def _ics_version(sequence: int | None, modified_at: datetime | None) -> str | None:
    """Die Fassung einer ICS-Zeile.

    Ein iCalendar-Abo hat kein ETag im eigentlichen Sinn. ``SEQUENCE`` ist das
    nächste, was man hat, und wenn es fehlt, ist es ``LAST-MODIFIED``. Fehlt auch
    das, bleibt es bei ``None``, und jede Übernahme schreibt den Termin neu -
    korrekt, nur nicht als "geändert" erkennbar. Wichtig ist nur, dass die
    lokale Änderung trotzdem geschützt bleibt; dafür sorgt ``sync_state``.
    """
    if sequence is not None:
        return f"seq-{sequence}"
    if modified_at is not None:
        return f"mod-{modified_at.isoformat()}"
    return None


def _guard_size(text: str, size_text: str) -> str:
    if len(text.encode("utf-8")) > MAX_ICAL_BYTES:
        raise CalendarConfigError(f"Die ICS-Quelle ist mit {size_text} zu groß für einen Abruf.")
    return text


def _overlaps(start: datetime, end: datetime, window_start: datetime, window_end: datetime) -> bool:
    return as_utc(start) < window_end and as_utc(end) > window_start
