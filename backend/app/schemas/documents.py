from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import UTCResponseModel


class DocumentRead(UTCResponseModel):
    id: int
    filename: str
    source: str
    status: str
    external_id: str | None = None
    mime_type: str | None = None
    created_at: datetime
    processed_at: datetime | None = None


class DocumentSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=20)


class DocumentSourceOut(BaseModel):
    document_id: int
    filename: str
    chunk_index: int
    content: str


class DocumentHitOut(DocumentSourceOut):
    distance: float | None = None


class DocumentSearchResponse(BaseModel):
    query: str
    hits: list[DocumentHitOut]


class DocumentAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=20)


class DocumentAskResponse(BaseModel):
    """Die Antwort auf eine Frage an die eigenen Dokumente.

    ``grades_shared`` sagt, ob der Notenstand im Prompt stand. Es ist bewusst
    ein eigenes Feld und **kein** Wert von ``source``: ``source`` beschreibt,
    woher die Antwort kommt (``ai``/``fallback``/``none``), und diese drei Werte
    bleiben unveraendert. Die Frage "wurden meine Noten mitgeschickt" ist eine
    andere als die Frage "hat ein Modell geschrieben" - und sie ist die, die
    eine Lernende stellen muss, wenn es um ihre Noten geht.
    """

    answer: str
    source: str
    model: str | None = None
    sources: list[DocumentSourceOut]
    grades_shared: bool = False