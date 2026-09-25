from datetime import datetime, timezone
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import UTCResponseModel

StudyTaskStatus = Literal["pending", "in_progress", "completed", "cancelled"]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class StudyTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_plan_id: int
    topic_id: int | None = None
    title: str = Field(min_length=1, max_length=240)
    description: str | None = None
    duration_minutes: int = Field(default=30, ge=1, le=1440)
    scheduled_for: datetime | None = None
    order_index: int = Field(default=0, ge=0)

    @field_validator("scheduled_for")
    @classmethod
    def normalize_scheduled_for(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value) if value is not None else None


class StudyTaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    scheduled_for: datetime | None = None
    status: StudyTaskStatus | None = Field(default=None)
    order_index: int | None = Field(default=None, ge=0)

    @field_validator("scheduled_for")
    @classmethod
    def normalize_scheduled_for(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        nullable_fields = {"topic_id", "description"}
        for field in self.model_fields_set - nullable_fields:
            if getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class StudyTaskCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=4000)


class StudyTaskRead(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    study_plan_id: int
    topic_id: int | None = None
    subject_name: str | None = None
    subject_color: str | None = None
    topic_name: str | None = None
    title: str
    description: str | None = None
    duration_minutes: int
    scheduled_for: datetime | None = None
    status: StudyTaskStatus
    order_index: int
    completed_at: datetime | None = None


class StudySessionRead(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    started_at: datetime
    completed_at: datetime | None = None
    duration_minutes: int | None = None
    notes: str | None = None
