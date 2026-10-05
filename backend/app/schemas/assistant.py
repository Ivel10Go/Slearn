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
    #: Aus der Chat-Antwort: der Eintrag wird dort als Signal vermerkt.
    training_example_id: int | None = None


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
    training_example_id: int | None = None


class AssistantRouteRead(BaseModel):
    integration_id: int | None = None
    local_model_id: str | None = None
    name: str
    model: str
    source: str
    selected: bool = False


class TrainingHardwareRead(BaseModel):
    eligible: bool
    backend: Literal["mlx", "cuda"] | None = None
    reason: str
    system: str
    machine: str
    ram_gb: float | None = None
    gpu_name: str | None = None
    vram_gb: float | None = None


class TrainingStatsRead(BaseModel):
    total: int
    good: int
    bad: int
    corrected: int
    unlabeled: int


class TrainingStatusRead(BaseModel):
    enabled: bool
    max_examples: int
    stats: TrainingStatsRead
    hardware: TrainingHardwareRead


class TrainingConsentUpdate(BaseModel):
    enabled: bool


class TrainingExampleRead(UTCResponseModel):
    id: int
    created_at: datetime
    question: str
    completion: str
    source: str
    model: str | None = None
    label: Literal["good", "bad", "corrected"] | None = None
    user_rating: Literal["good", "bad"] | None = None
    applied_actions: int
    rejected_actions: int
    memory_deleted: bool
    server_adjusted: bool


class TrainingFeedback(BaseModel):
    #: ``rejected`` = ein Vorschlag wurde verworfen; ``good``/``bad`` = Daumen;
    #: ``cleared`` nimmt den Daumen zurueck.
    event: Literal["rejected", "good", "bad", "cleared"]
