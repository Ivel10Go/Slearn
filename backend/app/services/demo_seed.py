from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import as_utc, utc_now
from app.db.entities import (
    CalendarEvent,
    Exam,
    Grade,
    Homework,
    LearningProgress,
    Lesson,
    Room,
    StudyPlan,
    StudyPlanTask,
    Subject,
    SyncSource,
    Teacher,
    Topic,
    User,
)

DEMO_SOURCE = "demo"


@dataclass(frozen=True, slots=True)
class DemoSeedResult:
    created: int
    updated: int

    @property
    def total(self) -> int:
        return self.created + self.updated


ModelType = TypeVar("ModelType")


def _find(db: Session, model: type[ModelType], **filters: Any) -> ModelType | None:
    return db.scalar(select(model).filter_by(**filters))


def _upsert(
    db: Session,
    model: type[ModelType],
    lookup: dict[str, Any],
    values: dict[str, Any],
    result: dict[str, int],
) -> ModelType:
    instance = _find(db, model, **lookup)
    if instance is None:
        instance = model(**lookup, **values)
        db.add(instance)
        result["created"] += 1
    else:
        for key, value in values.items():
            setattr(instance, key, value)
        result["updated"] += 1
    db.flush()
    return instance


def demo_external_ids() -> tuple[str, ...]:
    return (
        "demo-teacher-math",
        "demo-teacher-history",
        "demo-room-101",
        "demo-subject-math",
        "demo-subject-history",
        "demo-topic-derivatives",
        "demo-topic-industrial-revolution",
        "demo-lesson-0",
        "demo-lesson-1",
        "demo-homework-0",
        "demo-homework-1",
        "demo-exam-0",
        "demo-event-0",
        "demo-plan",
        "demo-task-0",
        "demo-task-1",
        "demo-grade-0",
        "demo-grade-1",
        "demo-grade-2",
        "demo-grade-3",
        "demo-grade-4",
    )


def _day_at(base: datetime, day_offset: int, hour: int, minute: int = 0) -> datetime:
    local = as_utc(base)
    return (local + timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)


def seed_demo_data(db: Session, user_id: int, now: datetime | None = None) -> DemoSeedResult:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("Cannot seed demo data for an unknown user")
    current = as_utc(now or utc_now())
    counts = {"created": 0, "updated": 0}

    source = _upsert(
        db,
        SyncSource,
        {"user_id": user_id, "source_type": DEMO_SOURCE, "name": "Demo"},
        {"enabled": True, "configuration": {"kind": "demo"}, "last_sync_at": current},
        counts,
    )
    math_teacher = _upsert(
        db,
        Teacher,
        {"user_id": user_id, "source": DEMO_SOURCE, "external_id": "demo-teacher-math"},
        {"name": "Dr. Beispiel", "email": None},
        counts,
    )
    history_teacher = _upsert(
        db,
        Teacher,
        {"user_id": user_id, "source": DEMO_SOURCE, "external_id": "demo-teacher-history"},
        {"name": "Prof. Demo", "email": None},
        counts,
    )
    room = _upsert(
        db,
        Room,
        {"user_id": user_id, "source": DEMO_SOURCE, "external_id": "demo-room-101"},
        {"name": "101", "building": "Hauptgebäude"},
        counts,
    )
    math = _upsert(
        db,
        Subject,
        {"user_id": user_id, "source": DEMO_SOURCE, "external_id": "demo-subject-math"},
        {
            "name": "Mathematik",
            "color": "#2563EB",
            "teacher_id": math_teacher.id,
            "description": "Grundlagen der Mathematik",
        },
        counts,
    )
    history = _upsert(
        db,
        Subject,
        {"user_id": user_id, "source": DEMO_SOURCE, "external_id": "demo-subject-history"},
        {
            "name": "Geschichte",
            "color": "#D97706",
            "teacher_id": history_teacher.id,
            "description": "Epochen und Verbindungen",
        },
        counts,
    )
    math_topic = _upsert(
        db,
        Topic,
        {"user_id": user_id, "subject_id": math.id, "name": "Derivative"},
        {"description": "Ableitungen und Anwendungen"},
        counts,
    )
    history_topic = _upsert(
        db,
        Topic,
        {"user_id": user_id, "subject_id": history.id, "name": "Industrielle Revolution"},
        {"description": "Ursachen und Folgen"},
        counts,
    )
    lesson_specs = [
        ("demo-lesson-0", math, math_teacher, room, 0, 8, 0, 45, "Analysis"),
        ("demo-lesson-1", history, history_teacher, room, 0, 10, 0, 45, "Zeitalter"),
    ]
    for (
        external_id,
        subject,
        teacher,
        lesson_room,
        day_offset,
        start_hour,
        start_minute,
        duration,
        title,
    ) in lesson_specs:
        start = _day_at(current, day_offset, start_hour, start_minute)
        _upsert(
            db,
            Lesson,
            {"user_id": user_id, "source": DEMO_SOURCE, "external_id": external_id},
            {
                "subject_id": subject.id,
                "teacher_id": teacher.id,
                "room_id": lesson_room.id,
                "sync_source_id": source.id,
                "title": title,
                "start_time": start,
                "end_time": start + timedelta(minutes=duration),
            },
            counts,
        )
    homework_specs = [
        ("demo-homework-0", math, "Derivationsübungen", "Aufgaben 1 bis 8 bearbeiten", 1, 18),
        ("demo-homework-1", history, "Quellenvergleich", "Die wichtigsten Quellen vergleichen", 3, 18),
    ]
    for external_id, subject, title, description, day_offset, hour in homework_specs:
        _upsert(
            db,
            Homework,
            {"user_id": user_id, "source": DEMO_SOURCE, "external_id": external_id},
            {
                "subject_id": subject.id,
                "sync_source_id": source.id,
                "title": title,
                "description": description,
                "due_date": _day_at(current, day_offset, hour),
                "completed": False,
                "completed_at": None,
            },
            counts,
        )
    _upsert(
        db,
        Exam,
        {"user_id": user_id, "source": DEMO_SOURCE, "external_id": "demo-exam-0"},
        {
            "subject_id": math.id,
            "sync_source_id": source.id,
            "title": "Analysis-Klausur",
            "description": "Derivative und Integrale",
            "date": _day_at(current, 7, 9),
        },
        counts,
    )
    event_start = _day_at(current, 2, 16)
    _upsert(
        db,
        CalendarEvent,
        {"user_id": user_id, "source": DEMO_SOURCE, "external_id": "demo-event-0"},
        {
            "sync_source_id": source.id,
            "title": "Lerngruppe",
            "description": "Gemeinsam Mathematik wiederholen",
            "start_time": event_start,
            "end_time": event_start + timedelta(hours=1),
            "all_day": False,
        },
        counts,
    )
    plan = _upsert(
        db,
        StudyPlan,
        {"user_id": user_id, "title": "Demo-Lernplan"},
        {
            "description": "Automatisch erzeugter Demo-Plan",
            "status": "active",
            "starts_on": current,
            "ends_on": current + timedelta(days=14),
        },
        counts,
    )
    task_specs = [
        ("demo-task-0", math_topic, "Ableitungen üben", 30, 0, 17),
        ("demo-task-1", history_topic, "Epochen wiederholen", 20, 1, 17),
    ]
    for external_id, topic, title, duration, day_offset, hour in task_specs:
        task = _find(db, StudyPlanTask, user_id=user_id, study_plan_id=plan.id, title=title)
        if task is None:
            task = StudyPlanTask(
                user_id=user_id,
                study_plan_id=plan.id,
                topic_id=topic.id,
                title=title,
                description="Demo-Aufgabe",
                duration_minutes=duration,
                scheduled_for=_day_at(current, day_offset, hour),
                status="pending",
                order_index=0 if external_id.endswith("0") else 1,
            )
            db.add(task)
            counts["created"] += 1
        else:
            task.topic_id = topic.id
            task.description = "Demo-Aufgabe"
            task.duration_minutes = duration
            task.scheduled_for = _day_at(current, day_offset, hour)
            task.status = "pending"
            counts["updated"] += 1
        db.flush()
    _upsert(
        db,
        LearningProgress,
        {"user_id": user_id, "topic_id": math_topic.id},
        {"confidence": 0.35, "last_reviewed_at": current, "notes": "Demo"},
        counts,
    )
    _upsert(
        db,
        LearningProgress,
        {"user_id": user_id, "topic_id": history_topic.id},
        {"confidence": 0.2, "last_reviewed_at": current, "notes": "Demo"},
        counts,
    )
    # Fuenf Noten ueber die letzten fuenf Wochen: zwei im guten, zwei im mittleren
    # und eines im schlechten Fach. Sie geben der Karte eine Linie und eine
    # Hochrechnung, die etwas zu sagen haben - ohne sie waere der Demo-Stand eine
    # leere Karte, und eine leere Karte erklaert nichts.
    grade_specs = [
        ("demo-grade-0", math, 1.7, "written_exam", 1, -28),
        ("demo-grade-1", math, 2.3, "test", 2, -21),
        ("demo-grade-2", math, 2.0, "written_exam", 1, -7),
        ("demo-grade-3", history, 3.8, "test", 1, -14),
        ("demo-grade-4", history, 4.3, "oral", 1, -4),
    ]
    for external_id, subject, value, kind, weight, day_offset in grade_specs:
        _upsert(
            db,
            Grade,
            {"user_id": user_id, "source": DEMO_SOURCE, "external_id": external_id},
            {
                "subject_id": subject.id,
                "document_id": None,
                "grade_value": value,
                "kind": kind,
                "weight": weight,
                "graded_on": _day_at(current, day_offset, 0).date(),
                "comment": "Demo",
            },
            counts,
        )
    return DemoSeedResult(created=counts["created"], updated=counts["updated"])


def seed_demo_data_and_commit(db: Session, user_id: int, now: datetime | None = None) -> DemoSeedResult:
    result = seed_demo_data(db, user_id, now)
    db.commit()
    return result
