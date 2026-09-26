from datetime import date, datetime

from pydantic import ConfigDict

from app.schemas.common import UTCResponseModel


class LessonRead(UTCResponseModel):
    """Eine Unterrichtseinheit im Fenster, für das abgefragt wurde."""

    id: int
    subject_id: int | None = None
    subject_name: str
    subject_short_name: str | None = None
    subject_color: str
    teacher_name: str | None = None
    room_name: str | None = None
    title: str | None = None
    start_time: datetime
    end_time: datetime
    is_current: bool
    is_cancelled: bool = False


class LessonRangeResponse(UTCResponseModel):
    """Antwort von ``GET /api/v1/lessons`` mit den Stunden eines Zeitraums.

    ``range_start`` und ``range_end`` sind lokale Kalendertage in der Zeitzone des
    Benutzers. Sie sind **einschließlich**; das Feld heißt deshalb bewusst nicht
    ``end_exclusive``.
    """

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    timezone: str
    range_start: date
    range_end: date
    lessons: list[LessonRead]
