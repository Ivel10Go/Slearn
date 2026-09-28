from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.db.entities import Document
from app.schemas.documents import (
    DocumentAskRequest,
    DocumentAskResponse,
    DocumentHitOut,
    DocumentRead,
    DocumentSearchRequest,
    DocumentSearchResponse,
    DocumentSourceOut,
)
from app.services.documents import answer_question, search_chunks

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


@router.get("", response_model=list[DocumentRead])
def list_documents(current_user: CurrentUser, db: DatabaseSession) -> list[Document]:
    return list(
        db.scalars(
            select(Document)
            .where(Document.user_id == current_user.id)
            .order_by(Document.created_at.desc(), Document.id)
        ).all()
    )


@router.post("/search", response_model=DocumentSearchResponse)
def search_documents(
    payload: DocumentSearchRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> DocumentSearchResponse:
    hits = search_chunks(db, current_user.id, settings, payload.query, limit=payload.limit)
    return DocumentSearchResponse(
        query=payload.query,
        hits=[
            DocumentHitOut(
                document_id=hit.document_id,
                filename=hit.filename,
                chunk_index=hit.chunk_index,
                content=hit.content,
                distance=hit.distance,
            )
            for hit in hits
        ],
    )


@router.post("/ask", response_model=DocumentAskResponse)
def ask_documents(
    payload: DocumentAskRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> DocumentAskResponse:
    result = answer_question(db, current_user.id, settings, payload.question, limit=payload.limit)
    return DocumentAskResponse(
        answer=result.answer,
        source=result.source,
        model=result.model,
        sources=[
            DocumentSourceOut(
                document_id=source.document_id,
                filename=source.filename,
                chunk_index=source.chunk_index,
                content=source.content,
            )
            for source in result.sources
        ],
    )