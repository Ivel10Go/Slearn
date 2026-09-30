from datetime import datetime, timezone
from typing import Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import UTCResponseModel

StudyTaskStatus = Literal["pending", "in_progress", "completed", "cancelled"]
StudyPlanStatus = Literal["active", "paused", "finished"]
StudyTaskSeriesStatus = Literal["active", "finished", "cancelled"]
# Ohne "none": eine Serie ohne Wiederholung ist eine normale Aufgabe.
StudyTaskRecurrence = Literal["daily", "weekly", "biweekly", "monthly"]
StudyTaskOrder = Literal["priority", "due", "position"]

# 0 bis 5. Fuenf Stufen sind genug, um zu sortieren, ohne dass die
# Oberflaeche eine Rangliste fuehren muss, die niemand pflegt.
MAX_PRIORITY = 5

PatchModel = TypeVar("PatchModel", bound=BaseModel)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _reject_null_patch_values(payload: PatchModel, nullable_fields: set[str]) -> PatchModel:
    """Lehnt `{"feld": null}` ab, ausser fuer die ausdruecklich leeren Felder.

    Sonst koennte ein PATCH `title` auf `None` setzen und die Aufgabe
    bekäme keinen Namen mehr.
    """
    for field in payload.model_fields_set - nullable_fields:
        if getattr(payload, field) is None:
            raise ValueError(f"{field} must not be null")
    return payload


class StudyPlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    description: str | None = None
    status: StudyPlanStatus = "active"
    starts_on: datetime | None = None
    ends_on: datetime | None = None

    @field_validator("starts_on", "ends_on")
    @classmethod
    def normalize_window(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.starts_on is not None and self.ends_on is not None and self.ends_on < self.starts_on:
            raise ValueError("ends_on must not be before starts_on")
        return self


class StudyPlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = None
    status: StudyPlanStatus | None = Field(default=None)
    starts_on: datetime | None = None
    ends_on: datetime | None = None

    @field_validator("starts_on", "ends_on")
    @classmethod
    def normalize_window(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        return _reject_null_patch_values(self, {"description", "starts_on", "ends_on"})

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        starts_on = self.starts_on
        ends_on = self.ends_on
        if starts_on is not None and ends_on is not None and ends_on < starts_on:
            raise ValueError("ends_on must not be before starts_on")
        return self


class StudyPlanRead(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None = None
    status: StudyPlanStatus
    starts_on: datetime | None = None
    ends_on: datetime | None = None
    task_count: int = Field(default=0, ge=0)
    open_task_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class StudyTaskSeriesCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_plan_id: int
    topic_id: int | None = None
    title: str = Field(min_length=1, max_length=240)
    description: str | None = None
    recurrence: StudyTaskRecurrence
    duration_minutes: int = Field(default=20, ge=1, le=1440)
    priority: int = Field(default=0, ge=0, le=MAX_PRIORITY)
    starts_on: datetime
    until: datetime | None = None
    # Obergrenze der Occurrences. Endet die Serie, sobald `until` oder
    # `occurrence_limit` erreicht ist.
    occurrence_limit: int | None = Field(default=None, ge=1, le=1000)

    @field_validator("starts_on", "until")
    @classmethod
    def normalize_window(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.until is not None and self.until < self.starts_on:
            raise ValueError("until must not be before starts_on")
        return self


class StudyTaskSeriesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    priority: int | None = Field(default=None, ge=0, le=MAX_PRIORITY)
    until: datetime | None = None
    occurrence_limit: int | None = Field(default=None, ge=1, le=1000)
    status: Literal["active", "cancelled"] | None = Field(default=None)

    @field_validator("until")
    @classmethod
    def normalize_until(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        nullable = {"topic_id", "description", "until", "occurrence_limit"}
        return _reject_null_patch_values(self, nullable)


class StudyTaskSeriesRead(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    study_plan_id: int
    topic_id: int | None = None
    subject_name: str | None = None
    subject_color: str | None = None
    topic_name: str | None = None
    title: str
    description: str | None = None
    recurrence: StudyTaskRecurrence
    duration_minutes: int
    priority: int
    starts_on: datetime | None = None
    until: datetime | None = None
    next_due_on: datetime | None = None
    occurrences: int = Field(ge=0)
    occurrence_limit: int | None = None
    status: StudyTaskSeriesStatus
    created_at: datetime
    updated_at: datetime


class StudyTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_plan_id: int
    topic_id: int | None = None
    exam_id: int | None = None
    title: str = Field(min_length=1, max_length=240)
    description: str | None = None
    duration_minutes: int = Field(default=30, ge=1, le=1440)
    scheduled_for: datetime | None = None
    due_date: datetime | None = None
    priority: int = Field(default=0, ge=0, le=MAX_PRIORITY)
    order_index: int = Field(default=0, ge=0)

    @field_validator("scheduled_for", "due_date")
    @classmethod
    def normalize_datetimes(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value) if value is not None else None


class StudyTaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: int | None = None
    exam_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    scheduled_for: datetime | None = None
    due_date: datetime | None = None
    priority: int | None = Field(default=None, ge=0, le=MAX_PRIORITY)
    status: StudyTaskStatus | None = Field(default=None)
    order_index: int | None = Field(default=None, ge=0)

    @field_validator("scheduled_for", "due_date")
    @classmethod
    def normalize_datetimes(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        nullable = {"topic_id", "exam_id", "description", "scheduled_for", "due_date"}
        return _reject_null_patch_values(self, nullable)


class StudyTaskCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=4000)


class StudyTaskDependencyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depends_on_task_id: int


class StudyTaskDependencyRead(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: int
    depends_on_task_id: int
    created_at: datetime


class StudyTaskPrerequisite(UTCResponseModel):
    """Eine offene Vorbedingung, so weit, wie die Oberflaeche sie zeigen muss."""

    task_id: int
    title: str
    status: StudyTaskStatus
    due_date: datetime | None = None


class StudyTaskRead(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    study_plan_id: int
    topic_id: int | None = None
    subject_name: str | None = None
    subject_color: str | None = None
    topic_name: str | None = None
    exam_id: int | None = None
    exam_title: str | None = None
    series_id: int | None = None
    series_recurrence: StudyTaskRecurrence | None = None
    title: str
    description: str | None = None
    duration_minutes: int
    scheduled_for: datetime | None = None
    due_date: datetime | None = None
    priority: int = 0
    status: StudyTaskStatus
    order_index: int
    completed_at: datetime | None = None
    # Offene Vorbedingungen. Eine nicht leere Liste heisst: die Aufgabe ist
    # blockiert und darf noch nicht gestartet werden.
    blocked_by: list[StudyTaskPrerequisite] = Field(default_factory=list)
    # Aufgaben, die auf diese warten. Die Oberflaeche loescht damit die
    # Abhaengigkeit, wenn sie eine Aufgabe selbst entfernt.
    blocking_task_ids: list[int] = Field(default_factory=list)
    # Der Schnitt des Faches, an dem die Aufgabe haengt, und ob er bestanden
    # ist. Beides `None`, wenn es zu diesem Fach keine Noten gibt - nicht 0,0.
    # Diese beiden Felder sind der Grund, warum die Prioritaet einer erzeugten
    # Aufgabe ueberhaupt am Notenstand haengen kann: sie machen sichtbar, dass
    # eine Prioritaet nicht nur aus der Konfidenz kam.
    subject_average: float | None = None
    subject_passed: bool | None = None


class ExamTaskPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_plan_id: int
    duration_minutes: int = Field(default=30, ge=1, le=1440)
    # Wie viele Tage vor der Klausur der Plan zurueckreicht.
    lead_days: int = Field(default=14, ge=1, le=180)
    scheduled_hour: int = Field(default=17, ge=0, le=23)
    # Themen, fuer die im Plan schon eine Aufgabe an der Klausur haengt,
    # ueberspringen statt eine zweite zu erzeugen.
    only_missing: bool = True
    # Die Aufgaben in Lernreihenfolge verketten, damit der Plan eine
    # Reihenfolge hat und nicht nur eine Liste.
    chain_dependencies: bool = True


class ExamTaskPlanResult(UTCResponseModel):
    exam_id: int
    study_plan_id: int
    created_tasks: list[StudyTaskRead] = Field(default_factory=list)
    created_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    first_due_on: datetime | None = None
    last_due_on: datetime | None = None


class StudySessionRead(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    started_at: datetime
    completed_at: datetime | None = None
    duration_minutes: int | None = None
    notes: str | None = None
