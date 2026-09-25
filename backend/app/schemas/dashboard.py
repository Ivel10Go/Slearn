from datetime import datetime

from pydantic import ConfigDict, Field

from app.schemas.common import UTCResponseModel
from app.schemas.study import StudyTaskStatus


class DashboardTopic(UTCResponseModel):
    topic_id: int
    name: str
    confidence: float


class DashboardLesson(UTCResponseModel):
    id: int
    subject_id: int | None = None
    subject_name: str
    subject_color: str
    teacher_name: str | None = None
    room_name: str | None = None
    title: str | None = None
    start_time: datetime
    end_time: datetime
    is_current: bool


class DashboardExam(UTCResponseModel):
    id: int
    subject_id: int | None = None
    subject_name: str
    subject_color: str
    title: str
    date: datetime
    days_until: int = Field(ge=0)
    topics: list[DashboardTopic]


class DashboardHomework(UTCResponseModel):
    id: int
    subject_id: int | None = None
    subject_name: str
    subject_color: str
    title: str
    description: str | None = None
    due_date: datetime
    completed: bool


class DashboardStudyTask(UTCResponseModel):
    id: int
    subject_name: str
    subject_color: str
    topic_name: str | None = None
    title: str
    scheduled_for: datetime | None = None
    duration_minutes: int
    status: StudyTaskStatus


class DashboardCalendarEvent(UTCResponseModel):
    id: int
    title: str
    description: str | None = None
    start_time: datetime
    end_time: datetime
    all_day: bool
    source: str


class DashboardSummary(UTCResponseModel):
    completed_lessons: int = Field(ge=0)
    total_lessons: int = Field(ge=0)
    day_progress_percent: float = Field(ge=0, le=100)
    open_homework: int = Field(ge=0)
    upcoming_exams: int = Field(ge=0)
    planned_minutes: int = Field(ge=0)


class DashboardResponse(UTCResponseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    timezone: str
    summary: DashboardSummary
    lessons: list[DashboardLesson]
    upcoming_exams: list[DashboardExam]
    homework: list[DashboardHomework]
    study_tasks: list[DashboardStudyTask]
    calendar_events: list[DashboardCalendarEvent]
