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
    #: Der Assistent handelt selbst: Vorschlaege werden gleich eingetragen und
    #: feste Befehle ("Plan meine Woche") ausgefuehrt, siehe ``services/agent.py``.
    auto_apply: bool = False


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


class AgentStepRead(BaseModel):
    """Etwas, das der Assistent selbst getan hat - mit ``undoable`` fuer "Rueckgaengig"."""

    kind: Literal["exam", "homework", "event", "homework_done", "week_plan"]
    id: int
    title: str
    message: str
    undoable: bool


class AgentUndoRequest(BaseModel):
    kind: Literal["exam", "homework", "event", "homework_done"]
    id: int
    #: Aus der Chat-Antwort: ein zurueckgenommener Eintrag zaehlt als verworfen.
    training_example_id: int | None = None


class AssistantChatResponse(BaseModel):
    reply: str
    source: str
    model: str | None = None
    sources: list[DocumentSourceOut]
    proposals: list[ActionProposalOut]
    memories_added: list[MemoryRead]
    grades_shared: bool = False
    training_example_id: int | None = None
    #: Was der Assistent in dieser Antwort selbst erledigt hat.
    done: list[AgentStepRead] = Field(default_factory=list)


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
    #: Im Hintergrund trainieren, wenn der Rechner am Netz haengt und ruht.
    auto: bool = False
    #: Aufschriebe und Arbeitsblaetter duerfen ins Training (eigene Freigabe).
    materials: bool = False
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


class TrainerSetupRead(BaseModel):
    """Die Trainer-Umgebung (``trainer_install.py``): fehlt, wird eingerichtet, ist bereit."""

    state: Literal["unsupported", "missing", "installing", "ready", "outdated", "error"]
    phase: str | None = None
    phase_label: str | None = None
    supported: bool
    reason: str | None = None
    error: str | None = None
    log_tail: list[str] = Field(default_factory=list)
    approx_bytes: int
    location: str
    torch_version: str | None = None
    installed_at: str | None = None
    manual_python: bool = False


class TrainingRunRead(BaseModel):
    """Eine Trainingsrunde, gelesen aus ihrem Ordner (``training_jobs.py``)."""

    run_id: str
    base_model_id: str
    status: Literal["pending", "loading", "generating", "running", "paused", "converting", "done", "failed"]
    #: Laeuft der Trainer gerade?
    active: bool
    step: int
    total_steps: int
    example_count: int
    loss: float | None = None
    eval_loss_base: float | None = None
    eval_loss: float | None = None
    #: Anteil der Pruefantworten mit richtiger Art und richtigem Datum (0-1).
    eval_score_base: float | None = None
    eval_score: float | None = None
    qa_done: int = 0
    qa_total: int = 0
    qa_kept: int = 0
    seconds_per_step: float | None = None
    skipped_too_long: int = 0
    error: str | None = None
    adapter_version: int | None = None
    activated: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class TrainingRunStart(BaseModel):
    #: Leer = das Modell, das der Assistent gerade benutzt.
    model_id: str | None = None


class AdapterVersionRead(BaseModel):
    version: int
    example_count: int
    eval_score: float | None = None
    eval_loss: float | None = None
    eval_loss_base: float | None = None
    created_at: str
    active: bool


class AdaptersRead(BaseModel):
    model_id: str
    active_version: int | None = None
    versions: list[AdapterVersionRead]


class TrainingAutoUpdate(BaseModel):
    enabled: bool


class TrainingConditions(BaseModel):
    """Von der Desktop-App: darf gerade trainiert werden (am Netz, Rechner ruht)?"""

    allowed: bool


class LearnerProfileRead(BaseModel):
    """Was der Assistent ueber das Lernen weiss - genau diese Zeilen stehen im Prompt."""

    lines: list[str]


class QuizStart(BaseModel):
    subject_id: int | None = None
    document_id: int | None = None


class QuizQuestionRead(BaseModel):
    question_id: int
    question: str
    document_id: int
    filename: str
    #: local | provider | fallback (Lueckenfrage ohne Modell)
    source: str


class QuizAnswerSubmit(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)


class QuizResultRead(BaseModel):
    verdict: Literal["richtig", "teilweise", "falsch"]
    explanation: str
    reference: str
    excerpt: str
    filename: str
    source: str
    topic: str | None = None
    confidence_label: str | None = None
