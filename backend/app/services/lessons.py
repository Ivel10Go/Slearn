"""Stundenplan über einen frei wählbaren Zeitraum, nicht nur für heute.

Das Dashboard liefert bewusst nur den heutigen Tag, weil Tagesfortschritt und
Tagesfokus dort berechnet werden. Für die Wochenvorschau braucht es dagegen ein
beliebiges Fenster - deshalb dieser eigene Pfad.
"""

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import Settings
from app.core.time import as_utc, day_window, resolve_timezone_name, utc_now, zone_or_utc
from app.db.entities import Lesson
from app.schemas.lessons import LessonRangeResponse, LessonRead
from app.services.academic import lesson_teacher_name, subject_values

MAX_RANGE_DAYS = 62


class LessonRangeError(ValueError):
    """Der angefragte Zeitraum ist unbrauchbar oder zu groß."""


def validate_range(start: date, end: date, max_days: int = MAX_RANGE_DAYS) -> None:
    if end < start:
        raise LessonRangeError("Das Ende des Zeitraums liegt vor dem Anfang.")
    if (end - start).days + 1 > max_days:
        raise LessonRangeError(f"Der Zeitraum darf höchstens {max_days} Tage umfassen.")


def serialize_lessons(
    db: Session,
    user_id: int,
    settings: Settings,
    start: date,
    end: date,
    now: datetime | None = None,
    user_timezone: str | None = None,
) -> LessonRangeResponse:
    """Lädt die Stunden zwischen ``start`` und ``end`` einschließlich.

    Beide Grenzen sind lokale Kalendertage in der Zeitzone des Benutzers. Die
    Abfrage nutzt den Index ``ix_lessons_user_start`` und filtert auf
    ``start_time``, damit eine Stunde genau einem Tag zugeordnet bleibt.
    """
    validate_range(start, end)
    current = as_utc(now or utc_now())
    timezone_name = resolve_timezone_name(user_timezone or settings.timezone, settings.timezone)
    local_zone = zone_or_utc(timezone_name)
    window_start, _ = day_window(start, local_zone)
    _, window_end = day_window(end, local_zone)

    lessons = list(
        db.scalars(
            select(Lesson)
            .options(joinedload(Lesson.subject), joinedload(Lesson.teacher), joinedload(Lesson.room))
            .where(
                Lesson.user_id == user_id,
                Lesson.start_time >= window_start,
                Lesson.start_time < window_end,
            )
            .order_by(Lesson.start_time, Lesson.id)
        ).all()
    )

    payload: list[LessonRead] = []
    for lesson in lessons:
        subject_name, subject_color = subject_values(lesson.subject)
        start_time = as_utc(lesson.start_time)
        end_time = as_utc(lesson.end_time)
        payload.append(
            LessonRead(
                id=lesson.id,
                subject_id=lesson.subject_id,
                subject_name=subject_name,
                subject_short_name=lesson.subject.short_name if lesson.subject else None,
                subject_color=subject_color,
                teacher_name=lesson_teacher_name(lesson),
                room_name=lesson.room.name if lesson.room else None,
                title=lesson.title,
                start_time=start_time,
                end_time=end_time,
                is_current=start_time <= current < end_time,
                is_cancelled=lesson.is_cancelled,
            )
        )

    return LessonRangeResponse(
        generated_at=current,
        timezone=timezone_name,
        range_start=start,
        range_end=end,
        lessons=payload,
    )
