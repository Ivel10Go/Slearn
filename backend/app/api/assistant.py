"""``/api/v1/assistant``: Chat, bestaetigte Eintraege und das Gedaechtnis.

Sechs Routen, alle auf den Sitzungsnutzer begrenzt:

* ``GET    /routes``           die waehlbaren KI-Verbindungen (ohne Netzabfrage)
* ``POST   /chat``             eine Antwort auf den Verlauf
* ``POST   /actions``          einen Vorschlag eintragen
* ``GET    /memories``         was sich der Assistent gemerkt hat
* ``POST   /memories``         einen Satz von Hand ergaenzen
* ``DELETE /memories/{id}``    einen Satz vergessen

Noten gehen wie bei der Dokumentenfrage nur mit Freigabe in den Prompt:
``grade_context_lines()`` ist ohne Freigabe leer.
"""

from typing import Literal, cast

from fastapi import APIRouter, HTTPException, status

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
)
from app.schemas.common import MessageResponse
from app.schemas.documents import DocumentSourceOut
from app.services import assistant as assistant_service
from app.services import grades as grades_service

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
            grade_context=grades_service.grade_context_lines(db, current_user.id),
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
    db.delete(memory)
    db.commit()
    return MessageResponse(message="Vergessen.")
