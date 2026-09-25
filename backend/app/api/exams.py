from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.api.deps import CurrentUser, DatabaseSession
from app.api.helpers import exam_dict, normalized_datetime, owned_or_404, subject_or_404
from app.db.entities import Exam
from app.schemas.academic import ExamCreate, ExamRead, ExamUpdate
from app.schemas.common import MessageResponse

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
