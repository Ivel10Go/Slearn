"""Wochenplaner: Stundenplan + offene Aufgaben + Klausuren -> Lernplan.

Der Planer verteilt Arbeit auf echte freie Zeit. Er kennt vier Quellen:

* **offene Lernaufgaben** ohne Termin (oder mit einem verpassten),
* **offene Hausaufgaben**, fuer die es noch keinen Block gibt,
* **anstehende Klausuren**, deren Themen noch keine Aufgabe haben,
* **faellige Wiederholungen**: Themen, deren ``next_review_on`` (aus
  ``progress.next_review_on``, also derselben Stufentabelle wie die
  Fortschrittskarte) im Zeitraum liegt oder schon verpasst ist. Ein Block
  ``review`` fruehestens an diesem Tag, Frist ist das Ende des Zeitraums,
  Prioritaet ``REVIEW_PRIORITY`` = 0 - nie ueber einem Klausurthema.

**Idempotenz der Wiederholungen** haengt am Thema, nicht am Titel: ein Thema
bekommt keinen Wiederholungsblock, solange es eine offene Aufgabe
(``pending``/``in_progress``) mit diesem ``topic_id`` und ohne
``homework_id`` gibt. Das trifft den eigenen Block vom letzten ``apply``
(``exam_id`` leer) ebenso wie ein offenes Klausurthema - wer das Thema ohnehin
fuer die Klausur lernt, wiederholt es dabei. Eine eigene Spalte waere eine
Migration fuer eine Frage, die die vorhandenen Felder schon beantworten.
Themen, die derselbe Lauf als Klausurthema plant, fallen ebenfalls heraus.

Frei ist, was zwischen ``earliest``/``latest`` liegt und weder Unterricht
noch Termin noch eine schon terminierte Aufgabe ist. Nach der letzten Stunde
gilt ``after_school_minutes`` Pause, zwischen zwei Bloecken ``break_minutes``.

Die Regeln, in der Reihenfolge, in der sie greifen:

1. **Abhaengigkeiten zuerst.** Eine Aufgabe wird erst geplant, wenn ihre
   Vorgaenger einen Platz haben, und liegt immer hinter ihnen.
2. **Frueheste Frist zuerst**, bei gleicher Frist die hoehere Prioritaet.
   Ohne Frist kommt eine Aufgabe nach allen mit Frist.
3. **Verteilt statt am Stueck.** Themen derselben Klausur landen moeglichst
   an verschiedenen Tagen - verteiltes Lernen haelt laenger als ein Abend
   mit allem. Passt das nicht vor die Klausur, gibt der Planer die Regel auf,
   bevor er eine Frist reisst.
4. **Tagesbudget.** Ein Tag bekommt hoechstens ``max_minutes_per_day``;
   schon terminierte Aufgaben zaehlen mit. Ein einzelner Block, der laenger
   ist als das Budget, darf einen sonst leeren Tag allein belegen.
5. **Lieber spaet als gar nicht.** Gibt es vor der Frist keinen Platz, kommt
   der Block auf den naechsten freien Platz danach und ist als ``late``
   markiert. Nur was im ganzen Zeitraum keinen Platz hat, steht in
   ``unplaced`` - mit Grund.

Der Planer ist deterministisch und ruft kein Modell. Eine Vorschau schreibt
nichts; die Uebernahme rechnet mit demselben Zustand dasselbe noch einmal und
traegt es ein.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.time import as_utc, day_window, utc_now
from app.db.entities import (
    CalendarEvent,
    Exam,
    Homework,
    LearningProgress,
    Lesson,
    StudyPlan,
    StudyPlanTask,
    StudyTaskDependency,
    Topic,
)
from app.schemas.planner import (
    PlannerBlock,
    PlannerBusy,
    PlannerDay,
    PlannerItemKind,
    PlannerRequest,
    PlannerResponse,
    PlannerUnplaced,
)
from app.services import grades as grades_service
from app.services import progress as progress_service
from app.services import study as study_service

DEFAULT_PLAN_TITLE = "Wochenplan"
HOMEWORK_PRIORITY = 3
# Unter jedem Klausurthema (``exam_priority`` liefert mindestens 0): eine
# Wiederholung ist Pflege, keine Frist.
REVIEW_PRIORITY = 0
REVIEW_MINUTES = 20
SLOT_STEP_MINUTES = 5


class PlannerError(Exception):
    """Ein Planungslauf, der an einer fremden oder fehlenden Ressource scheitert."""


@dataclass
class _Item:
    key: str
    kind: PlannerItemKind
    title: str
    minutes: int
    priority: int
    deadline: datetime | None = None
    not_before: datetime | None = None
    prereqs: list[str] = field(default_factory=list)
    # Ende eines Vorgaengers, der ausserhalb dieses Laufs fest liegt.
    after: datetime | None = None
    spread_key: str | None = None
    task: StudyPlanTask | None = None
    homework: Homework | None = None
    exam: Exam | None = None
    topic: Topic | None = None
    subject_name: str | None = None
    order: int = 0


@dataclass
class _Interval:
    start: datetime
    end: datetime


@dataclass
class _Day:
    day: date
    open_from: datetime
    open_until: datetime
    budget: int
    used: int = 0
    occupied: list[_Interval] = field(default_factory=list)
    busy: list[PlannerBusy] = field(default_factory=list)
    blocks: list[PlannerBlock] = field(default_factory=list)
    spread: set[str] = field(default_factory=set)


@dataclass
class _Placement:
    item: _Item
    start: datetime
    end: datetime
    late: bool


def _clock(value: str) -> time:
    hour, minute = value.split(":")
    return time(hour=int(hour), minute=int(minute))


def _round_up(value: datetime) -> datetime:
    """Auf den naechsten 5-Minuten-Schritt, damit kein Block um 16:07 beginnt."""
    if value.second or value.microsecond:
        value = value.replace(second=0, microsecond=0) + timedelta(minutes=1)
    remainder = value.minute % SLOT_STEP_MINUTES
    return value if remainder == 0 else value + timedelta(minutes=SLOT_STEP_MINUTES - remainder)


def _subject_name(topic: Topic | None) -> str | None:
    if topic is None or topic.subject is None:
        return None
    return topic.subject.name


# --------------------------------------------------------------------------
# Tage und freie Zeit
# --------------------------------------------------------------------------


def _build_days(
    db: Session, user_id: int, zone: ZoneInfo, request: PlannerRequest, now: datetime
) -> tuple[list[_Day], datetime]:
    """Die Tage des Zeitraums mit ihren festen Belegungen, und das Ende des Zeitraums."""
    first_day = now.astimezone(zone).date()
    last_day = first_day + timedelta(days=request.days - 1)
    window_start, _ = day_window(first_day, zone)
    _, window_end = day_window(last_day, zone)

    lessons = db.scalars(
        select(Lesson)
        .options(joinedload(Lesson.subject))
        .where(
            Lesson.user_id == user_id,
            Lesson.is_cancelled.is_(False),
            Lesson.start_time < window_end,
            Lesson.end_time > window_start,
        )
        .order_by(Lesson.start_time)
    ).all()
    # Ganztaegige Termine blockieren nichts: "Ferien" oder "Geburtstag Oma"
    # sagt nicht, wann am Tag keine Zeit ist.
    events = db.scalars(
        select(CalendarEvent).where(
            CalendarEvent.user_id == user_id,
            CalendarEvent.all_day.is_(False),
            CalendarEvent.start_time < window_end,
            CalendarEvent.end_time > window_start,
        )
    ).all()

    days: list[_Day] = []
    for offset in range(request.days):
        day = first_day + timedelta(days=offset)
        weekend = day.weekday() >= 5
        day_start, day_end = day_window(day, zone)
        begin = _clock(request.weekend_earliest if weekend else request.earliest)
        entry = _Day(
            day=day,
            open_from=as_utc(datetime.combine(day, begin, tzinfo=zone)),
            open_until=as_utc(datetime.combine(day, _clock(request.latest), tzinfo=zone)),
            budget=0 if weekend and not request.include_weekends else request.max_minutes_per_day,
        )

        todays_lessons = [
            lesson for lesson in lessons if as_utc(lesson.start_time) < day_end and as_utc(lesson.end_time) > day_start
        ]
        if todays_lessons:
            last_end = max(as_utc(lesson.end_time) for lesson in todays_lessons)
            entry.open_from = max(entry.open_from, last_end + timedelta(minutes=request.after_school_minutes))
        for lesson in todays_lessons:
            start, end = as_utc(lesson.start_time), as_utc(lesson.end_time)
            entry.occupied.append(_Interval(start, end))
            title = lesson.title or (lesson.subject.name if lesson.subject is not None else "Unterricht")
            entry.busy.append(PlannerBusy(kind="lesson", title=title, start=start, end=end))
        for event in events:
            start, end = as_utc(event.start_time), as_utc(event.end_time)
            if start < day_end and end > day_start:
                entry.occupied.append(_Interval(start, end))
                entry.busy.append(PlannerBusy(kind="event", title=event.title, start=start, end=end))
        if offset == 0:
            entry.open_from = max(entry.open_from, _round_up(now))
        days.append(entry)
    return days, window_end


def _occupy(entry: _Day, start: datetime, end: datetime, break_minutes: int) -> None:
    # Die Pause haengt an beiden Seiten: so haelt auch ein spaeter davor
    # gelegter Block den Abstand ein.
    pause = timedelta(minutes=break_minutes)
    entry.occupied.append(_Interval(start - pause, end + pause))


def _find_start(entry: _Day, earliest: datetime, minutes: int) -> datetime | None:
    """Fruehester Beginn an diesem Tag ab ``earliest``, an dem ``minutes`` frei sind."""
    length = timedelta(minutes=minutes)
    candidate = _round_up(max(earliest, entry.open_from))
    for interval in sorted(entry.occupied, key=lambda item: item.start):
        if interval.end <= candidate:
            continue
        if interval.start >= candidate + length:
            break
        candidate = _round_up(interval.end)
    if candidate + length > entry.open_until:
        return None
    return candidate


# --------------------------------------------------------------------------
# Arbeit einsammeln
# --------------------------------------------------------------------------


def _collect_tasks(
    db: Session, user_id: int, zone: ZoneInfo, request: PlannerRequest, days: list[_Day], now: datetime, window_end: datetime
) -> list[_Item]:
    """Offene Aufgaben aktiver Plaene. Schon terminierte werden zu fester Belegung."""
    items: list[_Item] = []
    by_task_id: dict[int, _Item] = {}
    fixed_end: dict[int, datetime] = {}

    tasks = db.scalars(
        select(StudyPlanTask)
        .join(StudyPlan, StudyPlan.id == StudyPlanTask.study_plan_id)
        .options(joinedload(StudyPlanTask.topic).joinedload(Topic.subject))
        .where(
            StudyPlanTask.user_id == user_id,
            # `in_progress` laeuft gerade - die verschiebt man nicht.
            StudyPlanTask.status == "pending",
            StudyPlan.status == "active",
        )
        .order_by(StudyPlanTask.id)
    ).all()
    done_homework = set(
        db.scalars(select(Homework.id).where(Homework.user_id == user_id, Homework.completed.is_(True))).all()
    )
    exam_ids = {task.exam_id for task in tasks if task.exam_id is not None}
    exam_titles = (
        {exam.id: exam.title for exam in db.scalars(select(Exam).where(Exam.id.in_(exam_ids))).all()} if exam_ids else {}
    )

    for task in tasks:
        if task.homework_id is not None and task.homework_id in done_homework:
            # Die Hausaufgabe ist anderswo abgehakt worden; ihr Block ist hinfaellig.
            continue
        scheduled = as_utc(task.scheduled_for) if task.scheduled_for is not None else None
        if scheduled is not None and scheduled >= window_end:
            # Schon fuer spaeter geplant - das ist eine Entscheidung, keine Luecke.
            continue
        if scheduled is not None and scheduled >= now and not request.replan_scheduled:
            end = scheduled + timedelta(minutes=task.duration_minutes)
            fixed_end[task.id] = end
            entry = next((day for day in days if day.day == scheduled.astimezone(zone).date()), None)
            if entry is not None:
                _occupy(entry, scheduled, end, request.break_minutes)
                entry.used += task.duration_minutes
                entry.busy.append(PlannerBusy(kind="task", title=task.title, start=scheduled, end=end))
            continue
        item = _Item(
            key=f"task:{task.id}",
            kind="task",
            title=task.title,
            minutes=task.duration_minutes,
            priority=task.priority,
            deadline=as_utc(task.due_date) if task.due_date is not None else None,
            spread_key=f"exam:{task.exam_id}" if task.exam_id is not None else None,
            task=task,
            subject_name=_subject_name(task.topic) or (exam_titles.get(task.exam_id) if task.exam_id else None),
        )
        items.append(item)
        by_task_id[task.id] = item

    if by_task_id:
        prerequisites = study_service.open_prerequisites_by_task(db, user_id, set(by_task_id))
        for task_id, prereqs in prerequisites.items():
            item = by_task_id[task_id]
            for prereq in prereqs:
                if prereq.id in by_task_id:
                    item.prereqs.append(f"task:{prereq.id}")
                elif prereq.id in fixed_end:
                    item.after = max(item.after or fixed_end[prereq.id], fixed_end[prereq.id])
    return items


def _collect_homework(
    db: Session, user_id: int, request: PlannerRequest, now: datetime, window_end: datetime
) -> list[_Item]:
    """Offene Hausaufgaben, faellig bis einen Tag nach dem Zeitraum, noch ohne Block."""
    linked = set(
        db.scalars(
            select(StudyPlanTask.homework_id).where(
                StudyPlanTask.user_id == user_id,
                StudyPlanTask.homework_id.is_not(None),
                StudyPlanTask.status != "cancelled",
            )
        ).all()
    )
    homework = db.scalars(
        select(Homework)
        .options(joinedload(Homework.subject))
        .where(
            Homework.user_id == user_id,
            Homework.completed.is_(False),
            Homework.due_date > now,
            # Faellig am Morgen nach dem Zeitraum heisst: am letzten Tag erledigen.
            Homework.due_date <= window_end + timedelta(days=1),
        )
        .order_by(Homework.due_date, Homework.id)
    ).all()
    return [
        _Item(
            key=f"homework:{entry.id}",
            kind="homework",
            title=f"Hausaufgabe: {entry.title}",
            minutes=request.homework_minutes,
            priority=HOMEWORK_PRIORITY,
            deadline=as_utc(entry.due_date),
            homework=entry,
            subject_name=entry.subject.name if entry.subject is not None else None,
        )
        for entry in homework
        if entry.id not in linked
    ]


def _collect_exams(
    db: Session, user_id: int, zone: ZoneInfo, request: PlannerRequest, now: datetime, window_end: datetime
) -> list[_Item]:
    """Themen anstehender Klausuren, deren Vorbereitungsfenster in den Zeitraum faellt.

    Reihenfolge, Prioritaet und Verkettung wie beim Klausur-Generator: das
    schwaechste Thema zuerst, jedes wartet auf das vorige. Eine Klausur ohne
    Themen bekommt einen allgemeinen Wiederholungsblock statt gar nichts.
    """
    lead = timedelta(days=request.exam_lead_days)
    exams = db.scalars(
        select(Exam)
        .options(joinedload(Exam.subject))
        .where(Exam.user_id == user_id, Exam.date > now, Exam.date < window_end + lead)
        .order_by(Exam.date, Exam.id)
    ).all()
    if not exams:
        return []
    planned_pairs = set(
        db.execute(
            select(StudyPlanTask.exam_id, StudyPlanTask.topic_id).where(
                StudyPlanTask.user_id == user_id,
                StudyPlanTask.exam_id.in_([exam.id for exam in exams]),
                StudyPlanTask.status != "cancelled",
            )
        ).tuples()
    )
    planned_exams = {exam_id for exam_id, _ in planned_pairs}
    subject_ids = {exam.subject_id for exam in exams if exam.subject_id is not None}
    averages = grades_service.subject_averages(db, user_id, subject_ids) if subject_ids else {}

    items: list[_Item] = []
    for exam in exams:
        exam_date = as_utc(exam.date)
        not_before = as_utc(datetime.combine((exam_date - lead).astimezone(zone).date(), time.min, tzinfo=zone))
        average = averages.get(exam.subject_id) if exam.subject_id is not None else None
        subject = exam.subject.name if exam.subject is not None else None
        topics = study_service.exam_topics(db, user_id, exam.subject_id) if exam.subject_id is not None else []
        if not topics:
            if exam.id not in planned_exams:
                items.append(
                    _Item(
                        key=f"exam:{exam.id}",
                        kind="exam_review",
                        title=f"Für {exam.title} wiederholen",
                        minutes=request.exam_topic_minutes,
                        priority=study_service.exam_priority(0.5, average),
                        deadline=exam_date,
                        not_before=not_before,
                        spread_key=f"exam:{exam.id}",
                        exam=exam,
                        subject_name=subject,
                    )
                )
            continue
        previous: str | None = None
        for topic in topics:
            if (exam.id, topic.id) in planned_pairs:
                continue
            confidence = float(topic.progress.confidence) if topic.progress is not None else 0.0
            key = f"exam:{exam.id}:topic:{topic.id}"
            items.append(
                _Item(
                    key=key,
                    kind="exam_topic",
                    title=f"{topic.name} lernen",
                    minutes=request.exam_topic_minutes,
                    priority=study_service.exam_priority(confidence, average),
                    deadline=exam_date,
                    not_before=not_before,
                    prereqs=[previous] if previous is not None else [],
                    spread_key=f"exam:{exam.id}",
                    exam=exam,
                    topic=topic,
                    subject_name=subject,
                )
            )
            previous = key
    return items


def _collect_reviews(
    db: Session, user_id: int, zone: ZoneInfo, days: list[_Day], window_end: datetime, skip_topics: set[int]
) -> list[_Item]:
    """Themen, deren naechste Wiederholung im Zeitraum faellig ist oder verpasst wurde.

    Der Termin kommt aus ``progress.next_review_on`` - dieselbe Stufentabelle wie
    auf ``/fortschritt``. Ohne ``last_reviewed_at`` gibt es keinen Termin und
    damit keinen Block.
    """
    first_day, last_day = days[0].day, days[-1].day
    open_topics = set(
        db.scalars(
            select(StudyPlanTask.topic_id).where(
                StudyPlanTask.user_id == user_id,
                StudyPlanTask.topic_id.is_not(None),
                StudyPlanTask.homework_id.is_(None),
                StudyPlanTask.status.in_(("pending", "in_progress")),
            )
        ).all()
    )
    rows = db.scalars(
        select(LearningProgress)
        .join(Topic, Topic.id == LearningProgress.topic_id)
        .options(joinedload(LearningProgress.topic).joinedload(Topic.subject))
        .where(
            LearningProgress.user_id == user_id,
            Topic.user_id == user_id,
            LearningProgress.last_reviewed_at.is_not(None),
        )
        .order_by(LearningProgress.topic_id)
    ).all()

    items: list[_Item] = []
    for row in rows:
        if row.topic_id in open_topics or row.topic_id in skip_topics:
            continue
        due_on = progress_service.next_review_on(row.last_reviewed_at, float(row.confidence), zone)
        if due_on is None or due_on > last_day:
            continue
        not_before, _ = day_window(max(first_day, due_on), zone)
        items.append(
            _Item(
                key=f"review:{row.topic_id}",
                kind="review",
                title=f"{row.topic.name} wiederholen",
                minutes=REVIEW_MINUTES,
                priority=REVIEW_PRIORITY,
                deadline=window_end,
                not_before=not_before,
                topic=row.topic,
                subject_name=_subject_name(row.topic),
            )
        )
    return items


# --------------------------------------------------------------------------
# Verteilen
# --------------------------------------------------------------------------


def _try_place(
    days: list[_Day], item: _Item, earliest: datetime, *, respect_deadline: bool, spread: bool
) -> tuple[_Day, datetime] | None:
    for entry in days:
        if entry.budget <= 0 or entry.open_until <= earliest:
            continue
        if entry.used > 0 and entry.used + item.minutes > entry.budget:
            continue
        if spread and item.spread_key is not None and item.spread_key in entry.spread:
            continue
        start = _find_start(entry, earliest, item.minutes)
        if start is None:
            continue
        if respect_deadline and item.deadline is not None and start + timedelta(minutes=item.minutes) > item.deadline:
            # Spaetere Tage liegen noch weiter hinter der Frist.
            return None
        return entry, start
    return None


def _schedule(
    items: list[_Item], days: list[_Day], request: PlannerRequest, now: datetime
) -> tuple[list[_Placement], list[tuple[_Item, str]]]:
    by_key = {item.key: item for item in items}
    waiting = {item.key: {key for key in item.prereqs if key in by_key} for item in items}
    dependents: dict[str, list[str]] = {item.key: [] for item in items}
    for item in items:
        for key in waiting[item.key]:
            dependents[key].append(item.key)

    far = datetime.max.replace(tzinfo=now.tzinfo)

    def rank(item: _Item) -> tuple[datetime, int, int]:
        return (item.deadline or far, -item.priority, item.order)

    heap = [(rank(item), item.key) for item in items if not waiting[item.key]]
    heapq.heapify(heap)
    placed: dict[str, _Placement] = {}
    unplaced: list[tuple[_Item, str]] = []
    failed: set[str] = set()

    def release(key: str) -> None:
        for dependent in dependents[key]:
            waiting[dependent].discard(key)
            if not waiting[dependent]:
                heapq.heappush(heap, (rank(by_key[dependent]), dependent))

    while heap:
        _, key = heapq.heappop(heap)
        item = by_key[key]
        blocked = [prereq for prereq in item.prereqs if prereq in failed]
        if blocked:
            failed.add(key)
            unplaced.append((item, f"Wartet auf „{by_key[blocked[0]].title}“, das keinen Platz hat"))
            release(key)
            continue
        earliest = now
        for bound in (item.not_before, item.after):
            if bound is not None and bound > earliest:
                earliest = bound
        for prereq in item.prereqs:
            if prereq in placed:
                earliest = max(earliest, placed[prereq].end + timedelta(minutes=request.break_minutes))

        found = (
            _try_place(days, item, earliest, respect_deadline=True, spread=True)
            or _try_place(days, item, earliest, respect_deadline=True, spread=False)
            or _try_place(days, item, earliest, respect_deadline=False, spread=False)
        )
        if found is None:
            failed.add(key)
            unplaced.append((item, "Kein freier Platz im Zeitraum"))
            release(key)
            continue
        entry, start = found
        end = start + timedelta(minutes=item.minutes)
        _occupy(entry, start, end, request.break_minutes)
        entry.used += item.minutes
        if item.spread_key is not None:
            entry.spread.add(item.spread_key)
        placed[key] = _Placement(item=item, start=start, end=end, late=item.deadline is not None and end > item.deadline)
        release(key)

    return sorted(placed.values(), key=lambda placement: placement.start), unplaced


# --------------------------------------------------------------------------
# Eintragen
# --------------------------------------------------------------------------


def _target_plan(db: Session, user_id: int, plan_id: int | None, *, create: bool) -> StudyPlan | None:
    if plan_id is not None:
        plan = db.get(StudyPlan, plan_id)
        if plan is None or plan.user_id != user_id:
            raise PlannerError("Study plan not found")
        return plan
    plan = db.scalars(
        select(StudyPlan)
        .where(StudyPlan.user_id == user_id, StudyPlan.title == DEFAULT_PLAN_TITLE, StudyPlan.status == "active")
        .order_by(StudyPlan.id)
        .limit(1)
    ).first()
    if plan is None and create:
        plan = StudyPlan(
            user_id=user_id,
            title=DEFAULT_PLAN_TITLE,
            description="Vom Wochenplaner angelegt: Hausaufgaben, Klausurvorbereitung und Wiederholungen.",
        )
        db.add(plan)
        db.flush()
    return plan


def _apply(
    db: Session, user_id: int, zone: ZoneInfo, plan: StudyPlan, placements: list[_Placement]
) -> tuple[dict[str, int], int]:
    """Vorhandene Aufgaben bekommen ihren Termin, neue Bloecke werden Aufgaben."""
    task_ids: dict[str, int] = {}
    order_index = study_service.next_order_index(db, plan.id)
    end_of_day = time(hour=23, minute=59)
    created = 0
    for placement in placements:
        item = placement.item
        if item.task is not None:
            item.task.scheduled_for = placement.start
            task_ids[item.key] = item.task.id
            continue
        task = StudyPlanTask(
            user_id=user_id,
            study_plan_id=plan.id,
            title=item.title[:240],
            duration_minutes=item.minutes,
            scheduled_for=placement.start,
            priority=item.priority,
            status="pending",
            order_index=order_index,
        )
        order_index += 1
        if item.homework is not None:
            task.homework_id = item.homework.id
            task.due_date = as_utc(item.homework.due_date)
            task.description = item.homework.description
        if item.exam is not None:
            task.exam_id = item.exam.id
            task.description = f"Vorbereitung auf {item.exam.title}"
            # Wie im Klausur-Generator: faellig am Ende des eigenen Lerntags.
            local_day = placement.start.astimezone(zone).date()
            task.due_date = as_utc(datetime.combine(local_day, end_of_day, tzinfo=zone))
        if item.topic is not None:
            task.topic_id = item.topic.id
        if item.kind == "review":
            # Ohne ``exam_id`` und ``homework_id``: genau daran erkennt der naechste
            # Lauf, dass die Wiederholung schon geplant ist.
            task.description = "Wiederholung nach Lernstand"
            task.due_date = item.deadline
        db.add(task)
        db.flush()
        task_ids[item.key] = task.id
        created += 1
        # Die Kette der Klausurthemen bleibt eine Kette, wie beim Generator.
        for prereq in item.prereqs:
            prereq_id = task_ids.get(prereq)
            if prereq_id is not None:
                db.add(StudyTaskDependency(user_id=user_id, task_id=task.id, depends_on_task_id=prereq_id))
    return task_ids, created


# --------------------------------------------------------------------------
# Einstieg
# --------------------------------------------------------------------------


def build_plan(
    db: Session,
    user_id: int,
    zone: ZoneInfo,
    request: PlannerRequest,
    *,
    apply: bool,
    now: datetime | None = None,
) -> PlannerResponse:
    """Plant den Zeitraum und traegt ihn mit ``apply=True`` ein (ohne Commit)."""
    moment = as_utc(now or utc_now())
    plan = _target_plan(db, user_id, request.study_plan_id, create=False)
    days, window_end = _build_days(db, user_id, zone, request, moment)
    exam_items = _collect_exams(db, user_id, zone, request, moment, window_end)
    items = [
        *_collect_tasks(db, user_id, zone, request, days, moment, window_end),
        *_collect_homework(db, user_id, request, moment, window_end),
        *exam_items,
    ]
    if request.include_reviews:
        planned_topics = {item.topic.id for item in exam_items if item.topic is not None}
        items.extend(_collect_reviews(db, user_id, zone, days, window_end, planned_topics))
    for index, item in enumerate(items):
        item.order = index
    placements, unplaced = _schedule(items, days, request, moment)

    task_ids: dict[str, int] = {}
    created = 0
    if apply and placements:
        plan = _target_plan(db, user_id, request.study_plan_id, create=True)
        assert plan is not None
        task_ids, created = _apply(db, user_id, zone, plan, placements)

    days_by_date = {entry.day: entry for entry in days}
    for placement in placements:
        item = placement.item
        task = item.task
        days_by_date[placement.start.astimezone(zone).date()].blocks.append(
            PlannerBlock(
                kind=item.kind,
                title=item.title,
                start=placement.start,
                end=placement.end,
                duration_minutes=item.minutes,
                priority=item.priority,
                deadline=item.deadline,
                late=placement.late,
                task_id=task_ids.get(item.key) or (task.id if task is not None else None),
                homework_id=item.homework.id if item.homework is not None else None,
                exam_id=item.exam.id if item.exam is not None else (task.exam_id if task is not None else None),
                topic_id=item.topic.id if item.topic is not None else (task.topic_id if task is not None else None),
                subject_name=item.subject_name,
            )
        )

    return PlannerResponse(
        applied=apply,
        study_plan_id=plan.id if plan is not None else None,
        window_start=days[0].day,
        window_end=days[-1].day,
        days=[
            PlannerDay(
                date=entry.day,
                budget_minutes=entry.budget,
                planned_minutes=entry.used,
                blocks=entry.blocks,
                busy=sorted(entry.busy, key=lambda busy: busy.start),
            )
            for entry in days
        ],
        unplaced=[
            PlannerUnplaced(
                kind=item.kind,
                title=item.title,
                duration_minutes=item.minutes,
                deadline=item.deadline,
                reason=reason,
                task_id=item.task.id if item.task is not None else None,
                homework_id=item.homework.id if item.homework is not None else None,
                exam_id=item.exam.id if item.exam is not None else None,
                topic_id=item.topic.id if item.topic is not None else None,
            )
            for item, reason in unplaced
        ],
        planned_count=len(placements),
        created_count=created,
    )
