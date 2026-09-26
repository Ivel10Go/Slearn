from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import Settings
from app.core.time import as_utc, day_window, resolve_timezone_name, utc_now, zone_or_utc
from app.db.entities import (
    CalendarEvent,
    Exam,
    Homework,
    Lesson,
    StudyPlanTask,
    Topic,
)
from app.schemas.dashboard import (
    DashboardCalendarEvent,
    DashboardExam,
    DashboardHomework,
    DashboardLesson,
    DashboardResponse,
    DashboardStudyTask,
    DashboardSummary,
    DashboardTopic,
)
from app.schemas.study import StudyTaskStatus
from app.services.academic import lesson_teacher_name, subject_values


def serialize_dashboard_payload(payload: Mapping[str, Any]) -> DashboardResponse:
    return DashboardResponse.model_validate(payload)


def serialize_dashboard(
    db: Session,
    user_id: int,
    settings: Settings,
    now: datetime | None = None,
    user_timezone: str | None = None,
) -> DashboardResponse:
    current = as_utc(now or utc_now())
    timezone_name = resolve_timezone_name(user_timezone or settings.timezone, settings.timezone)
    local_zone = zone_or_utc(timezone_name)
    local_now = current.astimezone(local_zone)
    day_start, day_end = day_window(local_now.date(), local_zone)
    lessons = list(
        db.scalars(
            select(Lesson)
            .options(joinedload(Lesson.subject), joinedload(Lesson.teacher), joinedload(Lesson.room))
            .where(Lesson.user_id == user_id, Lesson.start_time >= day_start, Lesson.start_time < day_end)
            .order_by(Lesson.start_time)
        ).all()
    )
    exams = list(
        db.scalars(
            select(Exam)
            .options(joinedload(Exam.subject))
            .where(Exam.user_id == user_id, Exam.date >= current)
            .order_by(Exam.date)
        ).all()
    )
    homework = list(
        db.scalars(
            select(Homework)
            .options(joinedload(Homework.subject))
            .where(Homework.user_id == user_id)
            .order_by(Homework.due_date, Homework.id)
            .limit(100)
        ).all()
    )
    study_tasks = [
        task
        for task in db.scalars(
            select(StudyPlanTask)
            .options(joinedload(StudyPlanTask.topic).joinedload(Topic.subject))
            .where(
                StudyPlanTask.user_id == user_id,
                or_(StudyPlanTask.scheduled_for.is_(None), StudyPlanTask.scheduled_for >= day_start),
            )
            .order_by(StudyPlanTask.scheduled_for.is_(None), StudyPlanTask.scheduled_for, StudyPlanTask.id)
            .limit(100)
        ).all()
        if task.status != "cancelled"
    ]
    calendar_events = list(
        db.scalars(
            select(CalendarEvent)
            .where(CalendarEvent.user_id == user_id, CalendarEvent.end_time >= current)
            .order_by(CalendarEvent.start_time, CalendarEvent.id)
            .limit(100)
        ).all()
    )

    subject_ids = {exam.subject_id for exam in exams if exam.subject_id is not None}
    topics_by_subject: dict[int, list[DashboardTopic]] = {}
    if subject_ids:
        topics = db.scalars(
            select(Topic)
            .options(joinedload(Topic.progress))
            .where(Topic.user_id == user_id, Topic.subject_id.in_(subject_ids))
            .order_by(Topic.name, Topic.id)
        ).all()
        for topic in topics:
            if topic.subject_id is None:
                continue
            progress = topic.progress
            topics_by_subject.setdefault(topic.subject_id, []).append(
                DashboardTopic(
                    topic_id=topic.id,
                    name=topic.name,
                    confidence=float(progress.confidence) if progress is not None else 0.0,
                )
            )

    dashboard_lessons: list[DashboardLesson] = []
    for lesson in lessons:
        subject_name, subject_color = subject_values(lesson.subject)
        dashboard_lessons.append(
            DashboardLesson(
                id=lesson.id,
                subject_id=lesson.subject_id,
                subject_name=subject_name,
                subject_short_name=lesson.subject.short_name if lesson.subject else None,
                subject_color=subject_color,
                teacher_name=lesson_teacher_name(lesson),
                room_name=lesson.room.name if lesson.room else None,
                title=lesson.title,
                start_time=as_utc(lesson.start_time),
                end_time=as_utc(lesson.end_time),
                is_current=as_utc(lesson.start_time) <= current < as_utc(lesson.end_time),
                is_cancelled=lesson.is_cancelled,
            )
        )

    dashboard_exams: list[DashboardExam] = []
    for exam in exams:
        subject_name, subject_color = subject_values(exam.subject)
        exam_date = as_utc(exam.date)
        dashboard_exams.append(
            DashboardExam(
                id=exam.id,
                subject_id=exam.subject_id,
                subject_name=subject_name,
                subject_color=subject_color,
                title=exam.title,
                date=exam_date,
                days_until=max(0, (exam_date.astimezone(local_zone).date() - local_now.date()).days),
                topics=topics_by_subject.get(exam.subject_id or 0, []),
            )
        )

    dashboard_homework = [
        DashboardHomework(
            id=item.id,
            subject_id=item.subject_id,
            subject_name=subject_values(item.subject)[0],
            subject_color=subject_values(item.subject)[1],
            title=item.title,
            description=item.description,
            due_date=as_utc(item.due_date),
            completed=item.completed,
        )
        for item in homework
    ]

    dashboard_tasks: list[DashboardStudyTask] = []
    for task in study_tasks:
        subject = task.topic.subject if task.topic else None
        subject_name, subject_color = subject_values(subject)
        dashboard_tasks.append(
            DashboardStudyTask(
                id=task.id,
                subject_name=subject_name,
                subject_color=subject_color,
                topic_name=task.topic.name if task.topic else None,
                title=task.title,
                scheduled_for=as_utc(task.scheduled_for) if task.scheduled_for else None,
                duration_minutes=task.duration_minutes,
                status=cast(StudyTaskStatus, task.status),
            )
        )

    dashboard_calendar = [
        DashboardCalendarEvent(
            id=event.id,
            title=event.title,
            description=event.description,
            start_time=as_utc(event.start_time),
            end_time=as_utc(event.end_time),
            all_day=event.all_day,
            source=event.source,
        )
        for event in calendar_events
    ]

    total_lessons = len(lessons)
    completed_lessons = sum(1 for lesson in lessons if as_utc(lesson.end_time) <= current)
    day_progress = round((completed_lessons / total_lessons) * 100, 2) if total_lessons else 0.0
    open_homework = [item for item in homework if not item.completed]
    open_tasks = [task for task in study_tasks if task.status != "completed"]
    planned_minutes = sum(
        task.duration_minutes
        for task in open_tasks
        if task.scheduled_for is not None and day_start <= as_utc(task.scheduled_for) < day_end
    )
    summary = DashboardSummary(
        completed_lessons=completed_lessons,
        total_lessons=total_lessons,
        day_progress_percent=day_progress,
        open_homework=len(open_homework),
        upcoming_exams=len(exams),
        planned_minutes=planned_minutes,
    )
    return DashboardResponse(
        generated_at=current,
        timezone=timezone_name,
        summary=summary,
        lessons=dashboard_lessons,
        upcoming_exams=dashboard_exams,
        homework=dashboard_homework,
        study_tasks=dashboard_tasks,
        calendar_events=dashboard_calendar,
    )


def dashboard_to_dict(response: DashboardResponse) -> dict[str, Any]:
    return response.model_dump(mode="json")
