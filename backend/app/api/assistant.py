"""``/api/v1/assistant``: Chat, bestaetigte Eintraege und das Gedaechtnis.

Alle Routen sind auf den Sitzungsnutzer begrenzt:

* ``GET    /routes``           die waehlbaren KI-Verbindungen (ohne Netzabfrage)
* ``POST   /chat``             eine Antwort auf den Verlauf
* ``POST   /actions``          einen Vorschlag eintragen
* ``GET    /memories``         was sich der Assistent gemerkt hat
* ``POST   /memories``         einen Satz von Hand ergaenzen
* ``DELETE /memories/{id}``    einen Satz vergessen
* ``/training/...``            Trainingsbeispiele (Freigabe, Liste, Signale,
                               Hardware), siehe ``app/services/training.py``

Noten gehen mit Freigabe in den Prompt - oder ohne, wenn nur lokale Modelle
antworten koennen (dann verlassen sie den Rechner nicht). Die Entscheidung
faellt in ``assistant_service.chat``.
"""

from dataclasses import asdict
from typing import Literal, cast

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.api.helpers import owned_or_404
from app.db.assistant import MEMORY_SOURCE_MANUAL, AssistantMemory
from app.schemas.assistant import (
    ActionApplyRequest,
    ActionApplyResponse,
    ActionProposalOut,
    AssistantChatRequest,
    AssistantChatResponse,
    AssistantRouteRead,
    MemoryCreate,
    MemoryRead,
    TrainingConsentUpdate,
    TrainingExampleRead,
    TrainingFeedback,
    TrainingHardwareRead,
    TrainingStatsRead,
    TrainingStatusRead,
)
from app.schemas.common import MessageResponse
from app.schemas.documents import DocumentSourceOut
from app.services import assistant as assistant_service
from app.services import grades as grades_service
from app.services import training as training_service
from app.services.training_hardware import detect_training_hardware

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])

_Kind = Literal["exam", "homework", "event"]


def _memory_read(memory: AssistantMemory) -> MemoryRead:
    return MemoryRead(id=memory.id, content=memory.content, source=memory.source, created_at=memory.created_at)


@router.get("/routes", response_model=list[AssistantRouteRead])
def list_routes(current_user: CurrentUser, db: DatabaseSession, settings: AppSettings) -> list[AssistantRouteRead]:
    return [
        AssistantRouteRead(
            integration_id=r.integration_id,
            local_model_id=r.local_model_id,
            name=r.name,
            model=r.model,
            source=r.source,
            selected=r.selected,
        )
        for r in assistant_service.available_routes(db, current_user.id, settings)
    ]


@router.post("/chat", response_model=AssistantChatResponse)
def chat(
    payload: AssistantChatRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> AssistantChatResponse:
    try:
        result = assistant_service.chat(
            db,
            current_user,
            settings,
            [assistant_service.ChatTurn(role=m.role, content=m.content) for m in payload.messages],
            integration_id=payload.integration_id,
            local_model_id=payload.local_model_id,
            voice=payload.voice,
            grade_context=grades_service.grade_detail_lines(db, current_user.id),
            grades_shared_online=grades_service.grades_sharing_enabled(db, current_user.id),
            record_training=training_service.training_enabled(db, current_user.id),
        )
    except assistant_service.AssistantActionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return AssistantChatResponse(
        reply=result.reply,
        source=result.source,
        model=result.model,
        grades_shared=result.grades_shared,
        sources=[
            DocumentSourceOut(
                document_id=s.document_id, filename=s.filename, chunk_index=s.chunk_index, content=s.content
            )
            for s in result.sources
        ],
        proposals=[
            ActionProposalOut(
                kind=cast(_Kind, p.kind),
                title=p.title,
                starts_at=p.starts_at,
                ends_at=p.ends_at,
                all_day=p.all_day,
                subject_id=p.subject_id,
                subject_name=p.subject_name,
                description=p.description,
            )
            for p in result.proposals
        ],
        memories_added=[_memory_read(m) for m in result.memories_added],
        training_example_id=result.training_example_id,
    )


@router.post("/actions", response_model=ActionApplyResponse, status_code=status.HTTP_201_CREATED)
def apply_action(payload: ActionApplyRequest, current_user: CurrentUser, db: DatabaseSession) -> ActionApplyResponse:
    proposal = assistant_service.ActionProposal(
        kind=payload.kind,
        title=payload.title,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        all_day=payload.all_day,
        subject_id=payload.subject_id,
        description=payload.description,
    )
    try:
        applied = assistant_service.apply_action(db, current_user.id, proposal)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found") from exc
    except assistant_service.AssistantActionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    # Ein fremdes oder geloeschtes Beispiel wird still uebergangen: der Eintrag
    # selbst ist geglueckt, und das Signal ist nur eine Zugabe.
    example = training_service.owned_example(db, current_user.id, payload.training_example_id)
    if example is not None:
        training_service.record_applied(
            example, payload.model_dump(mode="json", exclude={"training_example_id"})
        )
        db.commit()
    return ActionApplyResponse(
        kind=cast(_Kind, applied.kind),
        id=applied.id,
        title=applied.title,
        message=applied.message,
    )


@router.get("/memories", response_model=list[MemoryRead])
def list_memories(current_user: CurrentUser, db: DatabaseSession) -> list[MemoryRead]:
    return [_memory_read(m) for m in assistant_service.list_memories(db, current_user.id)]


@router.post("/memories", response_model=MemoryRead, status_code=status.HTTP_201_CREATED)
def create_memory(payload: MemoryCreate, current_user: CurrentUser, db: DatabaseSession) -> MemoryRead:
    memory = assistant_service.add_memory(db, current_user.id, payload.content, source=MEMORY_SOURCE_MANUAL)
    if memory is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Das weiß der Assistent schon.")
    db.commit()
    return _memory_read(memory)


@router.delete("/memories/{memory_id}", response_model=MessageResponse)
def delete_memory(memory_id: int, current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    memory = owned_or_404(db, AssistantMemory, memory_id, current_user.id)
    training_service.record_memory_deleted(db, memory)
    db.delete(memory)
    db.commit()
    return MessageResponse(message="Vergessen.")


# --- Training ----------------------------------------------------------------


def _training_status(db: DatabaseSession, user_id: int) -> TrainingStatusRead:
    return TrainingStatusRead(
        enabled=training_service.training_enabled(db, user_id),
        max_examples=training_service.MAX_EXAMPLES_PER_USER,
        stats=TrainingStatsRead(**asdict(training_service.training_stats(db, user_id))),
        hardware=TrainingHardwareRead(**asdict(detect_training_hardware())),
    )


@router.get("/training", response_model=TrainingStatusRead)
def training_status(current_user: CurrentUser, db: DatabaseSession) -> TrainingStatusRead:
    return _training_status(db, current_user.id)


@router.put("/training/consent", response_model=TrainingStatusRead)
def update_training_consent(
    payload: TrainingConsentUpdate, current_user: CurrentUser, db: DatabaseSession
) -> TrainingStatusRead:
    training_service.set_training_enabled(db, current_user.id, payload.enabled)
    db.commit()
    return _training_status(db, current_user.id)


@router.get("/training/examples", response_model=list[TrainingExampleRead])
def list_training_examples(
    current_user: CurrentUser,
    db: DatabaseSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[TrainingExampleRead]:
    return [
        TrainingExampleRead(
            id=e.id,
            created_at=e.created_at,
            question=training_service.question_of(e.prompt),
            completion=e.completion,
            source=e.source,
            model=e.model,
            label=cast(Literal["good", "bad", "corrected"] | None, training_service.example_label(e)),
            user_rating=cast(Literal["good", "bad"] | None, e.user_rating),
            applied_actions=len(e.applied_actions),
            rejected_actions=e.rejected_actions,
            memory_deleted=e.memory_deleted,
            server_adjusted=e.server_adjusted,
        )
        for e in training_service.list_examples(db, current_user.id, limit=limit, offset=offset)
    ]


@router.post("/training/examples/{example_id}/feedback", response_model=MessageResponse)
def training_feedback(
    example_id: int, payload: TrainingFeedback, current_user: CurrentUser, db: DatabaseSession
) -> MessageResponse:
    example = training_service.owned_example(db, current_user.id, example_id)
    if example is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    if payload.event == "rejected":
        training_service.record_rejected(example)
    else:
        training_service.set_rating(example, None if payload.event == "cleared" else payload.event)
    db.commit()
    return MessageResponse(message="Vermerkt.")


@router.delete("/training/examples/{example_id}", response_model=MessageResponse)
def delete_training_example(example_id: int, current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    example = training_service.owned_example(db, current_user.id, example_id)
    if example is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    db.delete(example)
    db.commit()
    return MessageResponse(message="Gelöscht.")


@router.delete("/training/examples", response_model=MessageResponse)
def delete_all_training_examples(current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    anzahl = training_service.delete_all_examples(db, current_user.id)
    db.commit()
    return MessageResponse(message=f"{anzahl} Beispiele gelöscht.")
