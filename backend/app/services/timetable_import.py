"""Importiert den WebUntis-Stundenplan in die lokale Datenbank.

Die Umsetzung trennt zwei concerns: der Adapter in ``webuntis.py`` weiß, wie
WebUntis antwortet, dieses Modul weiß, wie daraus Fächer, Lehrer, Räume und
Unterrichtseinheiten werden.

Zwei Entscheidungen sind bewusst so getroffen:

* **Ortszeit.** WebUntis liefert Datum und Uhrzeit in Schul-Ortszeit ohne
  Zeitzone. Umrechnet wird mit der Zeitzone des Nutzerkontos, weil ein
  Schülerkonto dieselbe Zeitzone hat wie seine Schule.
* **Ersetzen statt sammeln.** Innerhalb des abgefragten Zeitraums werden die
  importierten Einheiten vorher gelöscht. Dadurch ist ein Sync wiederholbar
  und eine in WebUntis gelöschte Stunde verschwindet auch hier. außerhalb des
  Zeitraums wird nichts angefasst, damit kein Sync aus Versehen alte Wochen
  mitnimmt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.time import as_utc, utc_now
from app.db.entities import Lesson, Room, Subject, SyncSource, Teacher
from app.services.lesson_choices import excluded_keys, slot_key
from app.services.webuntis import WebUntisEntity, WebUntisTimetable

SOURCE_NAME: str = "webuntis"
DEFAULT_SYNC_WEEKS: int = 4

_Named = TypeVar("_Named", Subject, Teacher, Room)


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """Was ein Sync geliefert hat, in Zahlen und in einem deutschen Satz."""

    imported_lessons: int
    subjects: int
    teachers: int
    rooms: int
    skipped_entries: int
    window_start: date
    window_end: date

    def describe(self) -> str:
        return (
            f"{self.imported_lessons} Unterrichtseinheiten für {self.window_start.strftime('%d.%m.%Y')} "
            f"bis {self.window_end.strftime('%d.%m.%Y')} importiert "
            f"({self.subjects} Fächer, {self.teachers} Lehrkräfte, {self.rooms} Räume)."
        )


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _localized(day: date, clock: time, zone: ZoneInfo) -> datetime:
    """Wandelt eine Schul-Ortszeit in den gespeicherten UTC-Zeitpunkt.

    Gespeichert wird durchgehend UTC, so wie es ``demo_seed`` und das Dashboard
    (``day_start``/``day_end`` in UTC) erwarten.
    """
    return as_utc(datetime.combine(day, clock, tzinfo=zone))


def import_timetable(
    db: Session,
    source: SyncSource,
    timetable: WebUntisTimetable,
    timezone_name: str,
) -> SyncOutcome:
    """Schreibt einen geladenen Stundenplan und ersetzt das abgefragte Fenster."""
    zone = _zone(timezone_name)
    if not source.enabled:
        raise ValueError("Die Integration ist deaktiviert.")

    window_start = timetable.window_start
    window_end = timetable.window_end
    scope_start = _localized(window_start, time.min, zone)
    scope_end = _localized(window_end + timedelta(days=1), time.min, zone)
    db.execute(
        delete(Lesson).where(
            Lesson.user_id == source.user_id,
            Lesson.sync_source_id == source.id,
            Lesson.start_time >= scope_start,
            Lesson.start_time < scope_end,
        )
    )

    subject_cache: dict[int, Subject] = {}
    teacher_cache: dict[int, Teacher] = {}
    room_cache: dict[int, Room] = {}

    excluded = excluded_keys(db, source.user_id)

    imported = 0
    for entry in timetable.entries:
        start = _localized(entry.day, entry.start, zone)
        end = _localized(entry.day, entry.end, zone)
        if end <= start:
            continue
        subject_id = _first_id(_resolve(db, subject_cache, Subject, source, entry.subjects, True))
        if subject_id is not None and slot_key(subject_id, start, end, zone) in excluded:
            # Eine Parallelstunde, die die Nutzerin abgewaehlt hat (Wahlfach,
            # Kurs): sie wird nicht eingetragen. Das Fach selbst bleibt angelegt.
            continue
        db.add(
            Lesson(
                user_id=source.user_id,
                sync_source_id=source.id,
                subject_id=subject_id,
                teacher_id=_first_id(_resolve(db, teacher_cache, Teacher, source, entry.teachers, True)),
                room_id=_first_id(_resolve(db, room_cache, Room, source, entry.rooms, False)),
                title=entry.lesson_text,
                start_time=start,
                end_time=end,
                source=SOURCE_NAME,
                is_cancelled=entry.is_cancelled,
                external_id=entry.external_id,
            )
        )
        imported += 1

    source.last_sync_at = utc_now()
    source.status = "connected"
    source.last_error = None
    db.commit()
    return SyncOutcome(
        imported_lessons=imported,
        subjects=len(subject_cache),
        teachers=len(teacher_cache),
        rooms=len(room_cache),
        skipped_entries=timetable.skipped,
        window_start=window_start,
        window_end=window_end,
    )


def _first_id(items: list[_Named]) -> int | None:
    return items[0].id if items else None


def _resolve(
    db: Session,
    cache: dict[int, _Named],
    model: type[_Named],
    source: SyncSource,
    entities: tuple[WebUntisEntity, ...],
    prefer_longname: bool,
) -> list[_Named]:
    """Liefert die DB-Zeilen zu den WebUntis-IDs und legt fehlende an.

    ``prefer_longname`` unterscheidet die Darstellungen: bei Fächern und Lehrern
    ist die ausgeschriebene Form (``Geographie - Ek``) hilfreicher als das
    Kürzel, bei Räumen ist dagegen die Nummer (``322``) das gesuchte Merkmal.
    """
    resolved: list[_Named] = []
    created_new = False
    for entity in entities:
        cached = cache.get(entity.id)
        if cached is not None:
            resolved.append(cached)
            continue
        found = db.scalar(
            select(model).where(
                model.user_id == source.user_id,
                model.source == SOURCE_NAME,
                model.external_id == str(entity.id),
            )
        )
        if found is None:
            name = entity.display(prefer_longname)
            # Ein gleichnamiger, manuell angelegter Eintrag wird wiederverwendet,
            # weil die Datenbank je Nutzer und Name eindeutig ist.
            existing_by_name = db.scalar(
                select(model).where(model.user_id == source.user_id, model.name == name)
            )
            if existing_by_name is not None:
                found = existing_by_name
            else:
                found = model(
                    user_id=source.user_id,
                    name=name,
                    source=SOURCE_NAME,
                    external_id=str(entity.id),
                )
                if isinstance(found, Subject):
                    found.short_name = entity.short()
                db.add(found)
                created_new = True
        if isinstance(found, Subject) and not found.short_name:
            # Nachtragen fuer Faecher aus aelteren Importen: die Zeile wurde
            # damals ueber den Namen wiederverwendet und nie befuellt.
            found.short_name = entity.short()
        cache[entity.id] = found
        resolved.append(found)

    if created_new:
        # Erst der Flush vergibt die Primärschlüssel, die die Lesson-Zeile
        # braucht; ohne ihn bleiben die Verweise auf neue Einträge leer.
        db.flush()
    return resolved
