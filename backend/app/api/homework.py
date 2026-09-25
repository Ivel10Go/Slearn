from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.api.deps import CurrentUser, DatabaseSession
from app.api.helpers import (
    homework_dict,
    normalized_datetime,
    owned_or_404,
    subject_or_404,
)
from app.core.time import utc_now
from app.db.entities import Homework
from app.schemas.academic import (
    HomeworkCompletedRequest,
    HomeworkCreate,
    HomeworkRead,
    HomeworkUpdate,
)
from app.schemas.common import MessageResponse

router = APIRouter(prefix="/api/v1/homework", tags=["homework"])


def _read(item: Homework) -> HomeworkRead:
    return HomeworkRead.model_validate(homework_dict(item))


@router.get("", response_model=list[HomeworkRead])
def list_homework(
    current_user: CurrentUser,
    db: DatabaseSession,
    completed: bool | None = Query(default=None),
) -> list[HomeworkRead]:
    query = select(Homework).options(joinedload(Homework.subject)).where(Homework.user_id == current_user.id)
    if completed is not None:
        query = query.where(Homework.completed.is_(completed))
    items = db.scalars(query.order_by(Homework.due_date)).all()
    return [_read(item) for item in items]


@router.post("", response_model=HomeworkRead, status_code=status.HTTP_201_CREATED)
def create_homework(payload: HomeworkCreate, current_user: CurrentUser, db: DatabaseSession) -> HomeworkRead:
    subject_or_404(db, payload.subject_id, current_user.id)
    values = payload.model_dump()
    if values["completed"]:
        values["completed_at"] = utc_now()
    item = Homework(user_id=current_user.id, **values)
    db.add(item)
    try:
        db.commit()
        db.refresh(item)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Homework already exists")
    return _read(item)


@router.get("/{homework_id}", response_model=HomeworkRead)
def get_homework(homework_id: int, current_user: CurrentUser, db: DatabaseSession) -> HomeworkRead:
    item = owned_or_404(db, Homework, homework_id, current_user.id)
    return _read(item)


@router.patch("/{homework_id}", response_model=HomeworkRead)
def update_homework(
    homework_id: int, payload: HomeworkUpdate, current_user: CurrentUser, db: DatabaseSession
) -> HomeworkRead:
    item = owned_or_404(db, Homework, homework_id, current_user.id)
    values = payload.model_dump(exclude_unset=True)
    if "subject_id" in values:
        subject_or_404(db, values["subject_id"], current_user.id)
    if "due_date" in values:
        values["due_date"] = normalized_datetime(values["due_date"])
    for key, value in values.items():
        setattr(item, key, value)
    try:
        db.commit()
        db.refresh(item)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Homework already exists")
    return _read(item)


def _set_completed(item: Homework, completed: bool) -> None:
    item.completed = completed
    item.completed_at = utc_now() if completed else None


@router.post("/{homework_id}/complete", response_model=HomeworkRead)
@router.post("/{homework_id}/toggle", response_model=HomeworkRead)
@router.patch("/{homework_id}/completed", response_model=HomeworkRead)
def set_homework_completed(
    homework_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    payload: HomeworkCompletedRequest | None = None,
) -> HomeworkRead:
    item = owned_or_404(db, Homework, homework_id, current_user.id)
    completed = not item.completed if payload is None else payload.completed
    _set_completed(item, completed)
    db.commit()
    db.refresh(item)
    return _read(item)


@router.delete("/{homework_id}", response_model=MessageResponse)
def delete_homework(homework_id: int, current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    item = owned_or_404(db, Homework, homework_id, current_user.id)
    db.delete(item)
    db.commit()
    return MessageResponse(message="Homework deleted")
