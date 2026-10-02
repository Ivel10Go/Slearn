from datetime import date

from pydantic import BaseModel, Field


class TimetableEntryDraft(BaseModel):
    """Eine wiederkehrende Stunde der Woche, so wie sie auf dem Plan steht."""

    weekday: int = Field(ge=0, le=6, description="0 = Montag")
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")
    subject: str = Field(min_length=1, max_length=160)
    short_name: str | None = Field(default=None, max_length=20)
    room: str | None = Field(default=None, max_length=120)


class TimetableSubjectDraft(BaseModel):
    name: str
    short_name: str | None = None
    exists: bool


class TimetableExtractResult(BaseModel):
    entries: list[TimetableEntryDraft]
    subjects: list[TimetableSubjectDraft]
    via: str = Field(description="`image` oder `pdf_text`")


class TimetableApplyRequest(BaseModel):
    entries: list[TimetableEntryDraft] = Field(min_length=1, max_length=200)
    weeks: int = Field(default=8, ge=1, le=26)


class TimetableApplyResult(BaseModel):
    created_subjects: int
    imported_lessons: int
    window_start: date
    window_end: date


class LessonChoiceOption(BaseModel):
    subject_id: int
    name: str
    short_name: str | None = None
    selected: bool


class LessonChoiceGroup(BaseModel):
    """Mehrere Faecher zur selben Zeit: eines oder mehrere davon werden besucht."""

    key: str
    weekday: int = Field(description="0 = Montag")
    start: str
    end: str
    decided: bool
    options: list[LessonChoiceOption]


class LessonChoiceSelection(BaseModel):
    key: str
    attended: list[int] = Field(min_length=1, description="IDs der besuchten Faecher")


class LessonChoiceRequest(BaseModel):
    selections: list[LessonChoiceSelection] = Field(min_length=1, max_length=100)


class LessonChoiceResult(BaseModel):
    groups: list[LessonChoiceGroup]
    resynced: bool = Field(description="Ob neu abgerufen wurde, weil eine Abwahl zurueckgenommen wurde")
