from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DatabaseSession
from app.api.helpers import owned_or_404
from app.db.entities import Subject, Teacher
from app.schemas.academic import SubjectCreate, SubjectRead, SubjectUpdate
from app.schemas.common import MessageResponse

router = APIRouter(prefix="/api/v1/subjects", tags=["subjects"])


@router.get("", response_model=list[SubjectRead])
def list_subjects(current_user: CurrentUser, db: DatabaseSession) -> list[Subject]:
    return list(db.scalars(select(Subject).where(Subject.user_id == current_user.id).order_by(Subject.name)).all())


@router.post("", response_model=SubjectRead, status_code=status.HTTP_201_CREATED)
def create_subject(payload: SubjectCreate, current_user: CurrentUser, db: DatabaseSession) -> Subject:
    if payload.teacher_id is not None:
        teacher = db.get(Teacher, payload.teacher_id)
        if teacher is None or teacher.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Teacher not found")
    subject = Subject(user_id=current_user.id, **payload.model_dump())
    db.add(subject)
    try:
        db.commit()
        db.refresh(subject)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Subject already exists")
    return subject


@router.get("/{subject_id}", response_model=SubjectRead)
def get_subject(subject_id: int, current_user: CurrentUser, db: DatabaseSession) -> Subject:
    return owned_or_404(db, Subject, subject_id, current_user.id)


@router.patch("/{subject_id}", response_model=SubjectRead)
def update_subject(subject_id: int, payload: SubjectUpdate, current_user: CurrentUser, db: DatabaseSession) -> Subject:
    subject = owned_or_404(db, Subject, subject_id, current_user.id)
    values = payload.model_dump(exclude_unset=True)
    if values.get("teacher_id") is not None:
        teacher = db.get(Teacher, values["teacher_id"])
        if teacher is None or teacher.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Teacher not found")
    for key, value in values.items():
        setattr(subject, key, value)
    try:
        db.commit()
        db.refresh(subject)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Subject already exists")
    return subject


@router.delete("/{subject_id}", response_model=MessageResponse)
def delete_subject(subject_id: int, current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    subject = owned_or_404(db, Subject, subject_id, current_user.id)
    db.delete(subject)
    db.commit()
    return MessageResponse(message="Subject deleted")
