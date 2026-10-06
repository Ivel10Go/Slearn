from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.db.entities import Document, Subject
from app.schemas.documents import (
    DocumentAskRequest,
    DocumentAskResponse,
    DocumentHitOut,
    DocumentMaterialUpdate,
    DocumentRead,
    DocumentSearchRequest,
    DocumentSearchResponse,
    DocumentSourceOut,
)
from app.services import grades as grades_service
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


@router.patch("/{document_id}/material", response_model=DocumentRead)
def update_document_material(
    document_id: int, payload: DocumentMaterialUpdate, current_user: CurrentUser, db: DatabaseSession
) -> Document:
    """Korrigiert Art, Thema, Datum oder Fach. Von Hand Gesetztes ueberschreibt die Erkennung nie wieder."""
    document = db.get(Document, document_id)
    if document is None or document.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("subject_id") is not None:
        subject = db.get(Subject, changes["subject_id"])
        if subject is None or subject.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    for field, value in changes.items():
        setattr(document, field, value)
    extra = dict(document.extra_data or {})
    extra["manual_fields"] = sorted(set(extra.get("manual_fields", [])) | set(changes))
    document.extra_data = extra
    db.commit()
    db.refresh(document)
    return document


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
    """Beantwortet eine Frage aus den eigenen Dokumenten.

    **Noten gehen nur mit ausdruecklicher Freigabe mit.** ``grade_context_lines()``
    liefert ohne gesetzte Freigabe eine leere Liste, und eine leere Liste kommt
    nicht in den Prompt. Es gibt hier also keinen Zustand, in dem eine Note
    mitgeschickt wird, ohne dass die Lernende es vorher entschieden hat.

    ``source`` bleibt dreiwertig (``ai``/``fallback``/``none``) und beantwortet
    weiterhin nur die Frage, woher die Antwort stammt. Ob Noten dabeigewesen
    sind, sagt das eigene Feld ``grades_shared`` - zwei Fragen, zwei Felder.
    """
    grade_context = grades_service.grade_context_lines(db, current_user.id)
    result = answer_question(
        db,
        current_user.id,
        settings,
        payload.question,
        limit=payload.limit,
        grade_context=grade_context,
    )
    return DocumentAskResponse(
        answer=result.answer,
        source=result.source,
        model=result.model,
        grades_shared=result.grades_shared,
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