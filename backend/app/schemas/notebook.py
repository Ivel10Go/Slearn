from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UTCResponseModel

CreationKind = Literal["summary", "cheatsheet", "mindmap", "flashcards", "glossary", "practice_exam"]


class NotebookDocument(BaseModel):
    id: int
    filename: str
    subject_id: int | None
    material_kind: str | None
    material_topic: str | None
    selected: bool
    #: Nur die Kurzfassung geht ins Modell statt des ganzen Texts.
    summary: bool = False


class CreationRead(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    body: str
    items: list[dict[str, str]] | None
    source: str
    created_at: datetime


class NotebookRead(BaseModel):
    exam_id: int
    documents: list[NotebookDocument]
    creations: list[CreationRead]


class NotebookDocumentsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[int] = Field(max_length=500)
    #: Teilmenge von ``document_ids``, die nur als Kurzfassung mitgeht.
    summary_ids: list[int] = Field(default_factory=list, max_length=500)


class CreationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: CreationKind
