from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UTCResponseModel
from app.schemas.documents import DocumentSourceOut

ActionKind = Literal["exam", "homework", "event"]


class ChatMessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AssistantChatRequest(BaseModel):
    messages: list[ChatMessageIn] = Field(min_length=1, max_length=40)
    #: Genau diese KI-Verbindung verwenden. Ohne Angabe gilt die Reihenfolge
    #: aus ``ai_routing`` (lokal zuerst).
    integration_id: int | None = None
    #: Ein im Lernraum geladenes Textmodell, das eingebaut laeuft (``llama-server``).
    local_model_id: str | None = Field(default=None, max_length=64)
    #: Gespraechsmodus: kurze, vorlesbare Antwort.
    voice: bool = False


class ActionProposalOut(UTCResponseModel):
    kind: ActionKind
    title: str
    starts_at: datetime
    ends_at: datetime | None = None
    all_day: bool = False
    subject_id: int | None = None
    subject_name: str | None = None
    description: str | None = None


class ActionApplyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: ActionKind
    title: str = Field(min_length=1, max_length=240)
    starts_at: datetime
    ends_at: datetime | None = None
    all_day: bool = False
    subject_id: int | None = None
    description: str | None = Field(default=None, max_length=4000)


class ActionApplyResponse(BaseModel):
    kind: ActionKind
    id: int
    title: str
    message: str


class MemoryRead(UTCResponseModel):
    id: int
    content: str
    source: str
    created_at: datetime


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=400)


class AssistantChatResponse(BaseModel):
    reply: str
    source: str
    model: str | None = None
    sources: list[DocumentSourceOut]
    proposals: list[ActionProposalOut]
    memories_added: list[MemoryRead]
    grades_shared: bool = False


class AssistantRouteRead(BaseModel):
    integration_id: int | None = None
    local_model_id: str | None = None
    name: str
    model: str
    source: str
    selected: bool = False
