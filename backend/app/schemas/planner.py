from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import UTCResponseModel

# Was ein Block im Wochenplan ist:
# * ``task``        eine vorhandene, offene Lernaufgabe
# * ``homework``    eine offene Hausaufgabe, fuer die noch kein Block existiert
# * ``exam_topic``  ein Klausurthema, das noch keine Aufgabe hat
# * ``exam_review`` eine Klausur ohne Themen: ein allgemeiner Wiederholungsblock
PlannerItemKind = Literal["task", "homework", "exam_topic", "exam_review"]

CLOCK_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class PlannerRequest(BaseModel):
    """Die Rahmenbedingungen eines Planungslaufs.

    Alle Uhrzeiten sind Ortszeit des Nutzers als ``HH:MM``. Vorschau und
    Uebernehmen bekommen denselben Rumpf, damit die Uebernahme genau das
    eintraegt, was die Vorschau gezeigt hat.
    """

    model_config = ConfigDict(extra="forbid")

    # Ohne Angabe nimmt der Planer den Plan "Wochenplan" und legt ihn bei
    # Bedarf an. Vorhandene Aufgaben bleiben in ihrem eigenen Plan.
    study_plan_id: int | None = None
    days: int = Field(default=7, ge=1, le=28)
    earliest: str = Field(default="14:00", pattern=CLOCK_PATTERN)
    weekend_earliest: str = Field(default="10:00", pattern=CLOCK_PATTERN)
    latest: str = Field(default="19:30", pattern=CLOCK_PATTERN)
    include_weekends: bool = True
    max_minutes_per_day: int = Field(default=120, ge=15, le=600)
    break_minutes: int = Field(default=10, ge=0, le=60)
    # Abstand zwischen dem Ende der letzten Stunde und dem ersten Lernblock.
    after_school_minutes: int = Field(default=30, ge=0, le=240)
    homework_minutes: int = Field(default=30, ge=5, le=240)
    exam_topic_minutes: int = Field(default=45, ge=5, le=240)
    # Wie lange vor einer Klausur ihre Themen frühestens geplant werden.
    exam_lead_days: int = Field(default=10, ge=1, le=60)
    # Auch Aufgaben neu verteilen, die schon einen Termin in der Zukunft haben.
    replan_scheduled: bool = False

    @model_validator(mode="after")
    def latest_after_earliest(self) -> Self:
        if self.latest <= self.earliest or self.latest <= self.weekend_earliest:
            raise ValueError("Das Ende muss nach dem frühesten Beginn liegen")
        return self


class PlannerBlock(UTCResponseModel):
    kind: PlannerItemKind
    title: str
    start: datetime
    end: datetime
    duration_minutes: int
    priority: int
    deadline: datetime | None = None
    # Der Block liegt nach seiner Frist - es gab vorher keinen freien Platz.
    late: bool = False
    # Ohne Uebernahme nur bei ``kind == "task"`` gesetzt.
    task_id: int | None = None
    homework_id: int | None = None
    exam_id: int | None = None
    topic_id: int | None = None
    subject_name: str | None = None


class PlannerBusy(UTCResponseModel):
    """Was an einem Tag schon fest liegt: Unterricht, Termine, feste Aufgaben."""

    kind: Literal["lesson", "event", "task"]
    title: str
    start: datetime
    end: datetime


class PlannerDay(BaseModel):
    date: date
    budget_minutes: int
    planned_minutes: int
    blocks: list[PlannerBlock] = Field(default_factory=list)
    busy: list[PlannerBusy] = Field(default_factory=list)


class PlannerUnplaced(UTCResponseModel):
    kind: PlannerItemKind
    title: str
    duration_minutes: int
    deadline: datetime | None = None
    reason: str
    task_id: int | None = None
    homework_id: int | None = None
    exam_id: int | None = None
    topic_id: int | None = None


class PlannerResponse(BaseModel):
    applied: bool
    study_plan_id: int | None = None
    window_start: date
    window_end: date
    days: list[PlannerDay]
    unplaced: list[PlannerUnplaced]
    planned_count: int
    created_count: int = 0
