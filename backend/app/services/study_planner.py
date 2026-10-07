"""Der automatische Lernplan: Stundenplan + offene Aufgaben + Klausuren -> Lerntermine.

Ein deterministischer Planer, keine KI. Er soll in einem Absatz erklaerbar
sein und bei gleichen Daten denselben Plan liefern - ein Sprachmodell wuerde
bei "naechsten Donnerstag" rechnen muessen, und genau das kann es schlecht
(siehe ``date_from_text`` im Assistenten).

Ablauf:

1. **Freie Zeit je Tag.** Ein Schultag beginnt ``AFTER_SCHOOL_MINUTES`` nach
   der letzten (nicht ausgefallenen) Stunde, fruehestens um
   ``EARLIEST_SCHOOLDAY_HOUR``; ein Tag ohne Unterricht um ``FREE_DAY_HOUR``.
   Jeder Tag hat dasselbe Budget ``daily_minutes``. Bereits geplante offene
   Lernaufgaben verbrauchen es zuerst.
2. **Was ansteht**, jeweils mit einem Zeitfenster [frueheste, spaeteste Tag]:
   * offene Hausaufgaben im Horizont - bis zum Vortag der Abgabe,
   * Klausuren im Horizont - je Thema eine Einheit, das schwaechste zuerst
     (dieselbe Reihenfolge und Prioritaet wie der Klausur-Generator), bis zum
     Vortag; eine Klausur ohne Themen bekommt allgemeine Einheiten,
   * faellige Wiederholungen (``next_review_on`` im Horizont) - ab dem
     Wiederholungstag,
   * bisher ungeplante offene Lernaufgaben - bis zu ihrer Faelligkeit.
3. **Verteilen nach Frist**: was zuerst faellig ist, kommt zuerst dran; bei
   gleicher Frist das Wichtigere. Jede Einheit nimmt den fruehesten Tag ihres
   Fensters mit genug Restzeit. Passt sie nirgends, kommt sie auf den Tag mit
   der meisten Restzeit und der Plan nennt den Tag als ueberbucht - verschwinden
   darf nichts.

Idempotent: eine Hausaufgabe, ein Klausurthema oder eine Wiederholung, fuer die
schon eine offene Aufgabe besteht, wird uebersprungen. Ein zweiter Lauf legt
nur an, was neu dazugekommen ist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Final, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.time import as_utc, utc_now
from app.db.entities import Exam, Homework, Lesson, StudyPlan, StudyPlanTask, StudyTaskDependency, Topic
from app.services import grades as grades_service
from app.services.progress import next_review_on
from app.services.study import exam_priority, exam_topics, next_order_index, topic_priority

AFTER_SCHOOL_MINUTES: Final = 60
EARLIEST_SCHOOLDAY_HOUR: Final = 14
FREE_DAY_HOUR: Final = 10
#: Nach dieser Uhrzeit wird nichts mehr geplant, auch wenn Budget uebrig ist.
LATEST_START_HOUR: Final = 20
HOMEWORK_MINUTES: Final = 30
REVIEW_MINUTES: Final = 20
#: Wie viele allgemeine Einheiten eine Klausur ohne Themen bekommt.
GENERIC_EXAM_SESSIONS: Final = 3
#: So viele Tage vor einer Klausur beginnt die Vorbereitung hoechstens.
EXAM_LEAD_DAYS: Final = 10
OPEN_STATUSES: Final = ("pending", "in_progress")

Kind = Literal["homework", "exam", "review", "task"]


@dataclass
class PlanItem:
    kind: Kind
    title: str
    description: str | None
    duration_minutes: int
    earliest: date
    latest: date
    priority: int
    exam_id: int | None = None
    topic_id: int | None = None
    homework_id: int | None = None
    #: Eine vorhandene, bisher ungeplante Aufgabe - sie wird verschoben statt neu angelegt.
    task_id: int | None = None
    #: Laufende Nummer innerhalb einer Klausur, fuer die Verkettung.
    chain_key: int | None = None
    day: date | None = None
    starts_at: datetime | None = None


@dataclass
class DaySlot:
    day: date
    start: datetime
    capacity: int
    used: int = 0

    @property
    def remaining(self) -> int:
        return self.capacity - self.used


@dataclass
class PlanProposal:
    days: list[DaySlot]
    items: list[PlanItem]
    overbooked: list[date] = field(default_factory=list)
    skipped: int = 0


def _local_day_start(day: date, zone: ZoneInfo, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour=hour, minute=minute), tzinfo=zone)


def day_slots(
    db: Session, user_id: int, zone: ZoneInfo, first: date, days: int, daily_minutes: int
) -> list[DaySlot]:
    """Die Lernfenster je Tag aus dem Stundenplan."""
    window_start = as_utc(datetime.combine(first, time.min, tzinfo=zone))
    window_end = as_utc(datetime.combine(first + timedelta(days=days), time.min, tzinfo=zone))
    lessons = db.scalars(
        select(Lesson).where(
            Lesson.user_id == user_id,
            Lesson.is_cancelled.is_(False),
            Lesson.start_time >= window_start,
            Lesson.start_time < window_end,
        )
    ).all()
    last_end: dict[date, datetime] = {}
    for lesson in lessons:
        end = as_utc(lesson.end_time).astimezone(zone)
        key = as_utc(lesson.start_time).astimezone(zone).date()
        if key not in last_end or end > last_end[key]:
            last_end[key] = end
    slots: list[DaySlot] = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        if day in last_end:
            start = max(
                last_end[day] + timedelta(minutes=AFTER_SCHOOL_MINUTES),
                _local_day_start(day, zone, EARLIEST_SCHOOLDAY_HOUR),
            )
        else:
            start = _local_day_start(day, zone, FREE_DAY_HOUR)
        # Auf die naechste Viertelstunde, damit "14:50" nicht als Termin dasteht.
        quarter = (-start.minute) % 15
        start = (start + timedelta(minutes=quarter)).replace(second=0, microsecond=0)
        latest = _local_day_start(day, zone, LATEST_START_HOUR)
        capacity = daily_minutes if start < latest else 0
        slots.append(DaySlot(day=day, start=start, capacity=capacity))
    return slots


def _open_tasks(db: Session, user_id: int) -> list[StudyPlanTask]:
    return list(
        db.scalars(
            select(StudyPlanTask).where(StudyPlanTask.user_id == user_id, StudyPlanTask.status.in_(OPEN_STATUSES))
        ).all()
    )


def collect_items(
    db: Session, user_id: int, zone: ZoneInfo, first: date, last: date, open_tasks: list[StudyPlanTask]
) -> tuple[list[PlanItem], int]:
    """Alles, was im Horizont ansteht, ohne Doppelte. Liefert auch die Zahl der uebersprungenen."""
    items: list[PlanItem] = []
    skipped = 0
    covered_homework = {task.homework_id for task in open_tasks if task.homework_id is not None}
    covered_exam_topics = {(task.exam_id, task.topic_id) for task in open_tasks if task.exam_id is not None}
    covered_reviews = {task.topic_id for task in open_tasks if task.topic_id is not None and task.exam_id is None}
    horizon_end = as_utc(datetime.combine(last + timedelta(days=1), time.min, tzinfo=zone))
    horizon_start = as_utc(datetime.combine(first, time.min, tzinfo=zone))

    # Hausaufgaben: bis zum Vortag der Abgabe; eine fuer heute faellige noch heute.
    homeworks = db.scalars(
        select(Homework)
        .where(Homework.user_id == user_id, Homework.completed.is_(False), Homework.due_date < horizon_end)
        .order_by(Homework.due_date, Homework.id)
    ).all()
    for homework in homeworks:
        if homework.id in covered_homework:
            skipped += 1
            continue
        due = as_utc(homework.due_date).astimezone(zone).date()
        latest = max(first, min(last, due - timedelta(days=1)))
        items.append(
            PlanItem(
                kind="homework",
                title=f"Hausaufgabe: {homework.title}",
                description=homework.description,
                duration_minutes=HOMEWORK_MINUTES,
                earliest=first,
                latest=latest,
                priority=5 if due <= first + timedelta(days=1) else 3,
                homework_id=homework.id,
            )
        )

    # Klausuren: je Thema eine Einheit, das schwaechste zuerst.
    exams = db.scalars(
        select(Exam)
        .where(Exam.user_id == user_id, Exam.date >= horizon_start, Exam.date < horizon_end)
        .order_by(Exam.date, Exam.id)
    ).all()
    averages = grades_service.subject_averages(db, user_id, {exam.subject_id for exam in exams if exam.subject_id})
    for exam in exams:
        exam_day = as_utc(exam.date).astimezone(zone).date()
        latest = max(first, min(last, exam_day - timedelta(days=1)))
        earliest = max(first, exam_day - timedelta(days=EXAM_LEAD_DAYS))
        average = averages.get(exam.subject_id) if exam.subject_id is not None else None
        topics = exam_topics(db, user_id, exam.subject_id)
        if topics:
            for position, topic in enumerate(topics):
                if (exam.id, topic.id) in covered_exam_topics:
                    skipped += 1
                    continue
                confidence = float(topic.progress.confidence) if topic.progress is not None else 0.0
                items.append(
                    PlanItem(
                        kind="exam",
                        title=f"{topic.name} lernen",
                        description=f"Vorbereitung auf {exam.title}",
                        duration_minutes=45,
                        earliest=earliest,
                        latest=latest,
                        priority=exam_priority(confidence, average),
                        exam_id=exam.id,
                        topic_id=topic.id,
                        chain_key=position,
                    )
                )
        else:
            done = sum(1 for task in open_tasks if task.exam_id == exam.id and task.topic_id is None)
            skipped += min(done, GENERIC_EXAM_SESSIONS)
            for position in range(done, GENERIC_EXAM_SESSIONS):
                items.append(
                    PlanItem(
                        kind="exam",
                        title=f"Für {exam.title} lernen",
                        description="Lege Themen zur Klausur an, dann plant der Lernraum sie einzeln.",
                        duration_minutes=45,
                        earliest=earliest,
                        latest=latest,
                        priority=max(3, grades_service.grade_priority(average)),
                        exam_id=exam.id,
                        chain_key=position,
                    )
                )

    # Faellige Wiederholungen.
    topics_with_progress = db.scalars(
        select(Topic).options(joinedload(Topic.progress)).where(Topic.user_id == user_id).order_by(Topic.id)
    ).unique().all()
    for topic in topics_with_progress:
        progress = topic.progress
        if progress is None:
            continue
        due_on = next_review_on(progress.last_reviewed_at, float(progress.confidence), zone)
        if due_on is None or due_on > last:
            continue
        if topic.id in covered_reviews or any(item.topic_id == topic.id for item in items):
            skipped += 1
            continue
        items.append(
            PlanItem(
                kind="review",
                title=f"{topic.name} wiederholen",
                description=None,
                duration_minutes=REVIEW_MINUTES,
                earliest=max(first, due_on),
                latest=last,
                priority=topic_priority(float(progress.confidence)),
                topic_id=topic.id,
            )
        )

    # Bisher ungeplante offene Aufgaben.
    for task in open_tasks:
        if task.scheduled_for is not None:
            continue
        due = as_utc(task.due_date).astimezone(zone).date() if task.due_date is not None else last
        items.append(
            PlanItem(
                kind="task",
                title=task.title,
                description=task.description,
                duration_minutes=task.duration_minutes,
                earliest=first,
                latest=max(first, min(last, due)),
                priority=task.priority,
                task_id=task.id,
                exam_id=task.exam_id,
                topic_id=task.topic_id,
            )
        )
    return items, skipped


def distribute(items: list[PlanItem], slots: list[DaySlot]) -> list[date]:
    """Verteilt die Einheiten nach Frist; liefert die ueberbuchten Tage."""
    by_day = {slot.day: slot for slot in slots}
    overbooked: set[date] = set()
    order = sorted(
        range(len(items)),
        key=lambda index: (
            items[index].latest,
            -items[index].priority,
            items[index].exam_id or 0,
            items[index].chain_key if items[index].chain_key is not None else 0,
            index,
        ),
    )
    # Innerhalb einer Klausur kommt ein Thema nie vor seinem Vorgaenger dran.
    chain_floor: dict[int, date] = {}
    for index in order:
        item = items[index]
        earliest = item.earliest
        if item.exam_id is not None and item.chain_key is not None:
            earliest = max(earliest, chain_floor.get(item.exam_id, earliest))
        window = [by_day[day] for day in sorted(by_day) if earliest <= day <= max(earliest, item.latest)]
        if not window:
            window = [by_day[max(by_day)]]
        chosen = next((slot for slot in window if slot.remaining >= item.duration_minutes), None)
        if chosen is None:
            chosen = max(window, key=lambda slot: (slot.remaining, -slot.day.toordinal()))
            overbooked.add(chosen.day)
        item.day = chosen.day
        item.starts_at = chosen.start + timedelta(minutes=chosen.used)
        chosen.used += item.duration_minutes
        if item.exam_id is not None and item.chain_key is not None:
            chain_floor[item.exam_id] = chosen.day
    return sorted(overbooked)


def propose(
    db: Session, user_id: int, zone: ZoneInfo, days: int, daily_minutes: int, today: date | None = None
) -> PlanProposal:
    first = today or utc_now().astimezone(zone).date()
    last = first + timedelta(days=days - 1)
    slots = day_slots(db, user_id, zone, first, days, daily_minutes)
    open_tasks = _open_tasks(db, user_id)
    by_day = {slot.day: slot for slot in slots}
    for task in open_tasks:
        if task.scheduled_for is None:
            continue
        day = as_utc(task.scheduled_for).astimezone(zone).date()
        if day in by_day:
            by_day[day].used += task.duration_minutes
    items, skipped = collect_items(db, user_id, zone, first, last, open_tasks)
    overbooked = distribute(items, slots)
    items.sort(key=lambda item: (item.starts_at or datetime.max.replace(tzinfo=zone), item.title))
    return PlanProposal(days=slots, items=items, overbooked=overbooked, skipped=skipped)


def apply(db: Session, user_id: int, zone: ZoneInfo, proposal: PlanProposal, plan: StudyPlan) -> list[StudyPlanTask]:
    """Schreibt den Vorschlag: neue Aufgaben in ``plan``, vorhandene werden nur terminiert."""
    order_index = next_order_index(db, plan.id)
    written: list[StudyPlanTask] = []
    previous_in_exam: dict[int, StudyPlanTask] = {}
    end_of_day = time(hour=23, minute=59)
    for item in proposal.items:
        assert item.starts_at is not None and item.day is not None
        scheduled_for = as_utc(item.starts_at)
        if item.task_id is not None:
            task = db.get(StudyPlanTask, item.task_id)
            if task is None or task.user_id != user_id:
                continue
            task.scheduled_for = scheduled_for
            written.append(task)
            continue
        task = StudyPlanTask(
            user_id=user_id,
            study_plan_id=plan.id,
            topic_id=item.topic_id,
            exam_id=item.exam_id,
            homework_id=item.homework_id,
            title=item.title[:240],
            description=item.description,
            duration_minutes=item.duration_minutes,
            scheduled_for=scheduled_for,
            due_date=as_utc(datetime.combine(item.latest, end_of_day, tzinfo=zone)),
            priority=item.priority,
            status="pending",
            order_index=order_index,
        )
        order_index += 1
        db.add(task)
        db.flush()
        if item.exam_id is not None and item.chain_key is not None:
            previous = previous_in_exam.get(item.exam_id)
            if previous is not None:
                db.add(StudyTaskDependency(user_id=user_id, task_id=task.id, depends_on_task_id=previous.id))
            previous_in_exam[item.exam_id] = task
        written.append(task)
    return written

