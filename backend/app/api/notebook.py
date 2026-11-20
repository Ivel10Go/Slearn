"""Klassenarbeit als Arbeitsbereich: Material, Lernplan, Studio. Logik in ``app/services/notebook.py``."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentUser, DatabaseSession, RequestLanguage
from app.api.exams import generate_exam_tasks
from app.api.helpers import owned_or_404
from app.db.entities import Document, Exam, ExamCreation
from app.schemas.common import MessageResponse
from app.schemas.notebook import (
    CreationRead,
    CreationRequest,
    NotebookDocument,
    NotebookDocumentsWrite,
    NotebookRead,
)
from app.schemas.study import ExamTaskPlanRequest, ExamTaskPlanResult
from app.services import learner as learner_service
from app.services import notebook as notebook_service
from app.services import planner as planner_service

router = APIRouter(prefix="/api/v1/exams/{exam_id}/notebook", tags=["notebook"])


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("", response_model=NotebookRead)
def read_notebook(exam_id: int, current_user: CurrentUser, db: DatabaseSession) -> NotebookRead:
    exam = owned_or_404(db, Exam, exam_id, current_user.id)
    selected = {document.id for document in notebook_service.selected_documents(db, current_user.id, exam)}
    short = notebook_service.summary_ids(db, exam)
    documents = db.scalars(
        select(Document).where(Document.user_id == current_user.id).order_by(Document.created_at.desc())
    ).all()
    creations = db.scalars(
        select(ExamCreation)
        .where(ExamCreation.exam_id == exam.id, ExamCreation.user_id == current_user.id)
        .order_by(ExamCreation.created_at.desc(), ExamCreation.id.desc())
    ).all()
    return NotebookRead(
        exam_id=exam.id,
        documents=[
            NotebookDocument(
                id=document.id,
                filename=document.filename,
                subject_id=document.subject_id,
                material_kind=document.material_kind,
                material_topic=document.material_topic,
                selected=document.id in selected,
                summary=document.id in short,
            )
            for document in documents
        ],
        creations=[CreationRead.model_validate(creation) for creation in creations],
    )


@router.put("/documents", response_model=MessageResponse)
def update_documents(
    exam_id: int, payload: NotebookDocumentsWrite, current_user: CurrentUser, db: DatabaseSession
) -> MessageResponse:
    exam = owned_or_404(db, Exam, exam_id, current_user.id)
    try:
        notebook_service.set_documents(db, current_user.id, exam, payload.document_ids, payload.summary_ids)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found") from exc
    db.commit()
    return MessageResponse(message="Material gespeichert")


@router.post("/creations", response_model=CreationRead, status_code=status.HTTP_201_CREATED)
def create_creation(
    exam_id: int,
    payload: CreationRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    language: RequestLanguage,
) -> CreationRead:
    exam = owned_or_404(db, Exam, exam_id, current_user.id)
    generate = learner_service.routes_generator(db, current_user.id, settings)
    try:
        creation = notebook_service.create(db, current_user.id, exam, payload.kind, generate, language)
    except notebook_service.NotebookError as exc:
        raise _unprocessable(exc) from exc
    db.commit()
    return CreationRead.model_validate(creation)


@router.delete("/creations/{creation_id}", response_model=MessageResponse)
def delete_creation(exam_id: int, creation_id: int, current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    creation = owned_or_404(db, ExamCreation, creation_id, current_user.id)
    if creation.exam_id != exam_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    db.delete(creation)
    db.commit()
    return MessageResponse(message="Gelöscht")


@router.post("/plan", response_model=ExamTaskPlanResult, status_code=status.HTTP_201_CREATED)
def create_plan(
    exam_id: int, current_user: CurrentUser, db: DatabaseSession, settings: AppSettings
) -> ExamTaskPlanResult:
    """Themen aus dem Material anlegen, dann wie "Aufgaben erzeugen" in den Plan "Wochenplan"."""
    exam = owned_or_404(db, Exam, exam_id, current_user.id)
    try:
        notebook_service.ensure_topics(db, current_user.id, exam)
    except notebook_service.NotebookError as exc:
        raise _unprocessable(exc) from exc
    plan = planner_service._target_plan(db, current_user.id, None, create=True)
    assert plan is not None  # create=True legt ihn an
    db.commit()
    return generate_exam_tasks(exam.id, ExamTaskPlanRequest(study_plan_id=plan.id), current_user, db, settings)
