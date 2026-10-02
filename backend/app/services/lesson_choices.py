"""Parallele Stunden: auswaehlen, welche man besucht.

Im Schulportal liegen Wahl- und Kursstunden (Religion/Ethik, Sprachen,
Profilfaecher) zur selben Zeit nebeneinander - der Plan zeigt alle, besucht wird
eines. Ohne Auswahl stuenden im Kalender mehrere Faecher uebereinander.

Die Auswahl ist **je Zeitfenster** und nicht je Fach: ein Fach kann in einer
Parallelstunde abgewaehlt und in einer anderen Stunde regulaer besucht sein.
Gespeichert wird, was die Nutzerin **nicht** besucht, unter dem Schluessel der
Gruppe in ``settings.preferences['lesson_choices']`` - wie das Wochenziel, ohne
eigene Migration.

Zwei Dinge sind nicht offensichtlich:

* **Abgewaehlte Stunden werden geloescht, nicht versteckt.** Damit die Gruppe
  trotzdem weiter angezeigt wird (und man sich umentscheiden kann), werden
  die gespeicherten Abwahlen als virtuelle Stunden wieder in die Erkennung
  gemischt. Der Gruppenschluessel entsteht aus der **vollen** Menge und bleibt
  deshalb vor und nach der Entscheidung gleich.
* **Umentscheiden braucht einen neuen Abruf.** Eine geloeschte Stunde kommt nur
  ueber den Import zurueck, deshalb ruft der Endpunkt ihn nach einer
  Aufhebung selbst auf.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.time import as_utc
from app.db.entities import Lesson, Subject, UserSettings

PREFERENCE_KEY = "lesson_choices"
WINDOW_WEEKS = 4

# (subject_id, Wochentag, "HH:MM" Beginn, "HH:MM" Ende) in der Zeitzone der Nutzerin
SlotKey = tuple[int, int, str, str]


class LessonChoiceError(ValueError):
    """Eine Meldung, die so bei der Nutzerin ankommen darf."""


@dataclass(slots=True)
class ChoiceOption:
    subject_id: int
    name: str
    short_name: str | None
    slots: set[tuple[str, str]] = field(default_factory=set)
    selected: bool = True


@dataclass(slots=True)
class ConflictGroup:
    key: str
    weekday: int
    start: str
    end: str
    options: list[ChoiceOption]
    decided: bool


@dataclass(slots=True)
class _Item:
    subject_id: int
    start: datetime
    end: datetime


def _load(db: Session, user_id: int) -> dict[str, list[dict[str, Any]]]:
    row = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    preferences: Any = row.preferences if row is not None else None
    stored = preferences.get(PREFERENCE_KEY) if isinstance(preferences, dict) else None
    if not isinstance(stored, dict):
        return {}
    return {
        str(key): [item for item in value if isinstance(item, dict)]
        for key, value in stored.items()
        if isinstance(value, list)
    }


def _store(db: Session, user_id: int, choices: dict[str, list[dict[str, Any]]]) -> None:
    row = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if row is None:
        row = UserSettings(user_id=user_id)
        db.add(row)
    preferences: dict[str, Any] = dict(row.preferences) if isinstance(row.preferences, dict) else {}
    preferences[PREFERENCE_KEY] = choices
    row.preferences = preferences
    db.flush()


def excluded_keys(db: Session, user_id: int) -> set[SlotKey]:
    """Alle abgewaehlten Stunden - der Import ueberspringt genau diese."""
    keys: set[SlotKey] = set()
    for items in _load(db, user_id).values():
        for item in items:
            try:
                keys.add((int(item["subject_id"]), int(item["weekday"]), str(item["start"]), str(item["end"])))
            except (KeyError, TypeError, ValueError):
                continue
    return keys


def slot_key(subject_id: int, start: datetime, end: datetime, zone: ZoneInfo) -> SlotKey:
    local_start = as_utc(start).astimezone(zone)
    local_end = as_utc(end).astimezone(zone)
    return subject_id, local_start.weekday(), local_start.strftime("%H:%M"), local_end.strftime("%H:%M")


def _clusters(items: list[_Item]) -> list[list[_Item]]:
    """Gruppiert Stunden eines Tages, die sich zeitlich ueberlappen."""
    clusters: list[list[_Item]] = []
    cluster_end: datetime | None = None
    for item in sorted(items, key=lambda entry: (entry.start, entry.end)):
        if clusters and cluster_end is not None and item.start < cluster_end:
            clusters[-1].append(item)
            cluster_end = max(cluster_end, item.end)
        else:
            clusters.append([item])
            cluster_end = item.end
    return clusters


def find_groups(db: Session, user_id: int, zone: ZoneInfo, today: date | None = None) -> list[ConflictGroup]:
    """Die Parallelstunden der naechsten Wochen, je Zeitfenster einmal."""
    first_day = today or datetime.now(zone).date()
    last_day = first_day + timedelta(weeks=WINDOW_WEEKS)
    scope_start = as_utc(datetime.combine(first_day, time.min, tzinfo=zone))
    scope_end = as_utc(datetime.combine(last_day, time.min, tzinfo=zone))

    stored = _load(db, user_id)
    excluded = excluded_keys(db, user_id)
    subjects: dict[int, Subject | None] = {}

    lessons = db.scalars(
        select(Lesson).where(
            Lesson.user_id == user_id,
            Lesson.subject_id.is_not(None),
            Lesson.is_cancelled.is_(False),
            Lesson.start_time >= scope_start,
            Lesson.start_time < scope_end,
        )
    ).all()
    per_day: dict[date, list[_Item]] = {}
    for lesson in lessons:
        if lesson.subject_id is None:
            continue
        start = as_utc(lesson.start_time).astimezone(zone)
        end = as_utc(lesson.end_time).astimezone(zone)
        per_day.setdefault(start.date(), []).append(_Item(lesson.subject_id, start, end))

    # Abgewaehlte Stunden sind geloescht - als virtuelle Stunden zurueckholen.
    for subject_id, weekday, start_text, end_text in excluded:
        day = first_day
        while day < last_day:
            if day.weekday() == weekday:
                start = datetime.combine(day, time.fromisoformat(start_text), tzinfo=zone)
                end = datetime.combine(day, time.fromisoformat(end_text), tzinfo=zone)
                per_day.setdefault(day, []).append(_Item(subject_id, start, end))
            day += timedelta(days=1)

    groups: dict[str, ConflictGroup] = {}
    for day in sorted(per_day):
        for cluster in _clusters(per_day[day]):
            if len({item.subject_id for item in cluster}) < 2:
                continue
            weekday = day.weekday()
            signature = sorted(
                {(item.subject_id, item.start.strftime("%H:%M"), item.end.strftime("%H:%M")) for item in cluster}
            )
            key = f"{weekday}|" + ",".join(f"{sid}@{begin}-{finish}" for sid, begin, finish in signature)
            if key in groups:
                continue
            options: dict[int, ChoiceOption] = {}
            for item in cluster:
                option = options.get(item.subject_id)
                if option is None:
                    if item.subject_id not in subjects:
                        subjects[item.subject_id] = db.get(Subject, item.subject_id)
                    subject = subjects[item.subject_id]
                    if subject is None:
                        continue
                    option = options[item.subject_id] = ChoiceOption(item.subject_id, subject.name, subject.short_name)
                begin, finish = item.start.strftime("%H:%M"), item.end.strftime("%H:%M")
                option.slots.add((begin, finish))
                if (item.subject_id, weekday, begin, finish) in excluded:
                    option.selected = False
            if len(options) < 2:
                continue
            groups[key] = ConflictGroup(
                key=key,
                weekday=weekday,
                start=min(item.start for item in cluster).strftime("%H:%M"),
                end=max(item.end for item in cluster).strftime("%H:%M"),
                options=sorted(options.values(), key=lambda entry: entry.name.casefold()),
                decided=key in stored,
            )
    return sorted(groups.values(), key=lambda group: (group.weekday, group.start, group.key))


def save_choices(
    db: Session,
    user_id: int,
    zone: ZoneInfo,
    selections: dict[str, list[int]],
    today: date | None = None,
) -> bool:
    """Speichert die Auswahl und loescht abgewaehlte Stunden.

    ``selections`` bildet Gruppenschluessel auf die **besuchten** Fach-IDs ab.
    Gibt ``True`` zurueck, wenn eine frueher abgewaehlte Stunde wieder
    gewaehlt wurde - dann muss der Aufrufer neu abrufen, weil die Stunde
    geloescht ist.
    """
    groups = {group.key: group for group in find_groups(db, user_id, zone, today)}
    stored = _load(db, user_id)
    previously = excluded_keys(db, user_id)

    for key, attended in selections.items():
        group = groups.get(key)
        if group is None:
            raise LessonChoiceError("Diese Parallelstunde gibt es nicht mehr. Lade die Seite neu.")
        known = {option.subject_id for option in group.options}
        if not attended or not set(attended) <= known:
            raise LessonChoiceError("Wähle in jeder Parallelstunde mindestens ein Fach aus der Liste.")
        stored[key] = [
            {
                "subject_id": option.subject_id,
                "name": option.name,
                "weekday": group.weekday,
                "start": start,
                "end": end,
            }
            for option in group.options
            if option.subject_id not in attended
            for start, end in sorted(option.slots)
        ]

    _store(db, user_id, stored)
    now_excluded = excluded_keys(db, user_id)

    if now_excluded:
        subject_ids = {key[0] for key in now_excluded}
        rows = db.scalars(select(Lesson).where(Lesson.user_id == user_id, Lesson.subject_id.in_(subject_ids))).all()
        doomed = [
            lesson.id
            for lesson in rows
            if lesson.subject_id is not None
            and slot_key(lesson.subject_id, lesson.start_time, lesson.end_time, zone) in now_excluded
        ]
        if doomed:
            db.execute(delete(Lesson).where(Lesson.id.in_(doomed)))
    db.commit()
    return bool(previously - now_excluded)
