import re
from datetime import datetime, timezone
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import UTCResponseModel


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _valid_color(value: str | None) -> bool:
    return value is not None and re.fullmatch(r"#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{4}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})", value) is not None


def _reject_null_patch_values(value: BaseModel, nullable_fields: set[str]) -> None:
    for field in value.model_fields_set - nullable_fields:
        if getattr(value, field) is None:
            raise ValueError(f"{field} must not be null")


class SubjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    color: str = Field(default="#3B82F6", min_length=4, max_length=20)
    teacher_id: int | None = None
    description: str | None = None
    source: str = Field(default="manual", min_length=1, max_length=64)
    external_id: str | None = Field(default=None, max_length=255)

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str) -> str:
        if not _valid_color(value):
            raise ValueError("color must be a hexadecimal color")
        return value


class SubjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    color: str | None = Field(default=None, min_length=4, max_length=20)
    teacher_id: int | None = None
    description: str | None = None
    source: str | None = Field(default=None, min_length=1, max_length=64)
    external_id: str | None = Field(default=None, max_length=255)

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str | None) -> str | None:
        if value is not None and not _valid_color(value):
            raise ValueError("color must be a hexadecimal color")
        return value

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        _reject_null_patch_values(self, {"teacher_id", "description", "external_id"})
        return self


class SubjectRead(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    color: str
    teacher_id: int | None = None
    description: str | None = None
    source: str
    external_id: str | None = None
    created_at: datetime


class HomeworkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: int | None = None
    title: str = Field(min_length=1, max_length=240)
    description: str | None = None
    due_date: datetime
    completed: bool = False
    source: str = Field(default="manual", min_length=1, max_length=64)
    external_id: str | None = Field(default=None, max_length=255)

    @field_validator("due_date")
    @classmethod
    def normalize_due_date(cls, value: datetime) -> datetime:
        return _as_utc(value)


class HomeworkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = None
    due_date: datetime | None = None
    source: str | None = Field(default=None, min_length=1, max_length=64)
    external_id: str | None = Field(default=None, max_length=255)

    @field_validator("due_date")
    @classmethod
    def normalize_due_date(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        _reject_null_patch_values(self, {"subject_id", "description", "external_id"})
        return self


class HomeworkRead(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_id: int | None = None
    subject_name: str | None = None
    subject_color: str | None = None
    title: str
    description: str | None = None
    due_date: datetime
    completed: bool
    completed_at: datetime | None = None
    source: str
    external_id: str | None = None


class HomeworkCompletedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed: bool


class ExamCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: int | None = None
    title: str = Field(min_length=1, max_length=240)
    description: str | None = None
    date: datetime
    source: str = Field(default="manual", min_length=1, max_length=64)
    external_id: str | None = Field(default=None, max_length=255)

    @field_validator("date")
    @classmethod
    def normalize_date(cls, value: datetime) -> datetime:
        return _as_utc(value)


class ExamUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = None
    date: datetime | None = None
    source: str | None = Field(default=None, min_length=1, max_length=64)
    external_id: str | None = Field(default=None, max_length=255)

    @field_validator("date")
    @classmethod
    def normalize_date(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        _reject_null_patch_values(self, {"subject_id", "description", "external_id"})
        return self


class ExamRead(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_id: int | None = None
    subject_name: str | None = None
    subject_color: str | None = None
    title: str
    description: str | None = None
    date: datetime
    days_until: int | None = None
    source: str
    external_id: str | None = None


class CalendarEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    description: str | None = None
    start_time: datetime
    end_time: datetime
    all_day: bool = False
    source: str = Field(default="manual", min_length=1, max_length=64)
    external_id: str | None = Field(default=None, max_length=255)

    @field_validator("start_time", "end_time")
    @classmethod
    def normalize_times(cls, value: datetime) -> datetime:
        return _as_utc(value)


class CalendarEventUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    all_day: bool | None = None
    source: str | None = Field(default=None, min_length=1, max_length=64)
    external_id: str | None = Field(default=None, max_length=255)

    @field_validator("start_time", "end_time")
    @classmethod
    def normalize_times(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        _reject_null_patch_values(self, {"description", "external_id"})
        return self


class CalendarEventRead(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None = None
    start_time: datetime
    end_time: datetime
    all_day: bool
    source: str
    external_id: str | None = None
    #: Steht der Termin noch bei einer Gegenseite, oder wartet er nur darauf,
    #: dass es dort verschwindet? ``synced`` heißt: beide Seiten sind gleich.
    sync_state: str = "synced"
    #: Der Id der Integration, aus der der Termin stammt. ``None`` bei von Hand
    #: angelegten Terminen - die fasst kein Abruf an.
    sync_source_id: int | None = None


class CalendarWritingWrite(BaseModel):
    """Die ausdrückliche Zustimmung, Termine in einen Kalender zu schreiben.

    ``enabled`` ist Pflicht und hat **keinen** Default: ein fehlendes Feld darf
    keine bestehende Entscheidung versehentlich löschen, und ein Default von
    ``True`` wäre genau die stille Übertragung, die hier nicht stattfinden soll.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class CalendarWritingRead(UTCResponseModel):
    """Der Zustand der Zustimmung, mit dem Ort, an dem sie steht."""

    share_calendar_for_writing: bool
    preference_key: str
    uses: list[str] = Field(default_factory=list)


class CalendarFeedRead(UTCResponseModel):
    """Die abonnierbare Adresse des Lernraum-Kalenders.

    Der Schlüssel steht in der Antwort, weil nur die Eigentümerin diese Antwort
    bekommt und ohne ihn die Adresse nicht in einen Kalender eingetragen werden
    kann. Für jeden anderen ist er wertlos. ``rotated`` sagt, ob gerade ein neuer
    erzeugt wurde - der alte ist dann ab sofort ungültig.
    """

    url_path: str
    active: bool
    rotated: bool = False
    message: str = ""
