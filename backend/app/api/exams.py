from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.api.helpers import exam_dict, normalized_datetime, owned_or_404, subject_or_404
from app.core.time import resolve_timezone_name, zone_or_utc
from app.db.entities import Exam, StudyPlan
from app.schemas.academic import ExamCreate, ExamRead, ExamUpdate
from app.schemas.common import MessageResponse
from app.schemas.study import ExamTaskPlanRequest, ExamTaskPlanResult
from app.services import study as study_service
from app.services.study import StudyPlanError

router = APIRouter(prefix="/api/v1/exams", tags=["exams"])


def _read(item: Exam) -> ExamRead:
    return ExamRead.model_validate(exam_dict(item))


@router.get("", response_model=list[ExamRead])
def list_exams(
    current_user: CurrentUser,
    db: DatabaseSession,
    upcoming: bool = Query(default=True),
) -> list[ExamRead]:
    query = select(Exam).options(joinedload(Exam.subject)).where(Exam.user_id == current_user.id)
    if upcoming:
        from app.core.time import utc_now

        query = query.where(Exam.date >= utc_now())
    items = db.scalars(query.order_by(Exam.date)).all()
    return [_read(item) for item in items]


@router.post("", response_model=ExamRead, status_code=status.HTTP_201_CREATED)
def create_exam(payload: ExamCreate, current_user: CurrentUser, db: DatabaseSession) -> ExamRead:
    subject_or_404(db, payload.subject_id, current_user.id)
    values = payload.model_dump()
    values["date"] = normalized_datetime(values["date"])
    item = Exam(user_id=current_user.id, **values)
    db.add(item)
    try:
        db.commit()
        db.refresh(item)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Exam already exists")
    return _read(item)


@router.get("/{exam_id}", response_model=ExamRead)
def get_exam(exam_id: int, current_user: CurrentUser, db: DatabaseSession) -> ExamRead:
    return _read(owned_or_404(db, Exam, exam_id, current_user.id))


@router.patch("/{exam_id}", response_model=ExamRead)
def update_exam(exam_id: int, payload: ExamUpdate, current_user: CurrentUser, db: DatabaseSession) -> ExamRead:
    item = owned_or_404(db, Exam, exam_id, current_user.id)
    values = payload.model_dump(exclude_unset=True)
    if "subject_id" in values:
        subject_or_404(db, values["subject_id"], current_user.id)
    if "date" in values:
        values["date"] = normalized_datetime(values["date"])
    for key, value in values.items():
        setattr(item, key, value)
    try:
        db.commit()
        db.refresh(item)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Exam already exists")
    return _read(item)


@router.delete("/{exam_id}", response_model=MessageResponse)
def delete_exam(exam_id: int, current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    item = owned_or_404(db, Exam, exam_id, current_user.id)
    db.delete(item)
    db.commit()
    return MessageResponse(message="Exam deleted")


@router.post("/{exam_id}/tasks", response_model=ExamTaskPlanResult, status_code=status.HTTP_201_CREATED)
def generate_exam_tasks(
    exam_id: int,
    payload: ExamTaskPlanRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> ExamTaskPlanResult:
    """Aus den Klausurthemen Aufgaben in einem Lernplan erzeugen.

    Bisher verlinkte das Dashboard die Themen nur. Jetzt entsteht daraus
    ein Plan: eine Aufgabe je Thema, verteilt ueber die Tage vor der
    Klausur, sortiert nach Lernstand und in dieser Reihenfolge verkettet.

    Ohne ``exam.subject_id`` oder ohne Themen gibt es nichts zu erzeugen -
    das ist ein 422 mit Klartext, kein leerer 201.
    """
    exam = owned_or_404(db, Exam, exam_id, current_user.id)
    plan = owned_or_404(db, StudyPlan, payload.study_plan_id, current_user.id)
    zone = zone_or_utc(resolve_timezone_name(current_user.timezone, settings.timezone))
    try:
        created, skipped = study_service.generate_exam_tasks(
            db=db,
            exam=exam,
            plan=plan,
            user_id=current_user.id,
            zone=zone,
            duration_minutes=payload.duration_minutes,
            lead_days=payload.lead_days,
            scheduled_hour=payload.scheduled_hour,
            only_missing=payload.only_missing,
            chain_dependencies=payload.chain_dependencies,
        )
    except StudyPlanError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Study tasks could not be created"
        ) from exc
    for task in created:
        db.refresh(task)
    reads = study_service.read_tasks(db, current_user.id, created)
    due_dates = [task.due_date for task in reads if task.due_date is not None]
    return ExamTaskPlanResult(
        exam_id=exam.id,
        study_plan_id=plan.id,
        created_tasks=reads,
        created_count=len(reads),
        skipped_count=skipped,
        first_due_on=min(due_dates) if due_dates else None,
        last_due_on=max(due_dates) if due_dates else None,
    )
