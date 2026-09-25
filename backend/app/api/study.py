from datetime import datetime, timedelta
from typing import cast

from fastapi import APIRouter, Body, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.deps import CurrentUser, DatabaseSession
from app.api.helpers import normalized_datetime, owned_or_404
from app.core.time import as_utc, utc_now
from app.db.entities import StudyPlan, StudyPlanTask, StudySession, Topic
from app.schemas.common import MessageResponse
from app.schemas.study import (
    StudySessionRead,
    StudyTaskCompleteRequest,
    StudyTaskCreate,
    StudyTaskRead,
    StudyTaskStatus,
    StudyTaskUpdate,
)

router = APIRouter(prefix="/api/v1/study/tasks", tags=["study"])


def _read(task: StudyPlanTask) -> StudyTaskRead:
    subject = task.topic.subject if task.topic else None
    return StudyTaskRead(
        id=task.id,
        study_plan_id=task.study_plan_id,
        topic_id=task.topic_id,
        subject_name=subject.name if subject else None,
        subject_color=subject.color if subject else None,
        topic_name=task.topic.name if task.topic else None,
        title=task.title,
        description=task.description,
        duration_minutes=task.duration_minutes,
        scheduled_for=normalized_datetime(task.scheduled_for),
        status=cast(StudyTaskStatus, task.status),
        order_index=task.order_index,
        completed_at=normalized_datetime(task.completed_at),
    )


def _session_read(session: StudySession) -> StudySessionRead:
    return StudySessionRead.model_validate(session)


def _owned_task_for_update(db: Session, task_id: int, user_id: int) -> StudyPlanTask:
    task = db.scalar(
        select(StudyPlanTask)
        .where(StudyPlanTask.id == task_id, StudyPlanTask.user_id == user_id)
        .with_for_update()
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study task not found")
    return task


def _open_sessions(db: Session, user_id: int, task_id: int) -> list[StudySession]:
    return list(
        db.scalars(
            select(StudySession)
            .where(
                StudySession.user_id == user_id,
                StudySession.task_id == task_id,
                StudySession.completed_at.is_(None),
            )
            .order_by(StudySession.started_at, StudySession.id)
        ).all()
    )


def _elapsed_minutes(started_at: datetime, completed_at: datetime, minimum: int) -> int:
    seconds = max(0, int((as_utc(completed_at) - as_utc(started_at)).total_seconds()))
    return max(minimum, seconds // 60)


def _close_sessions(sessions: list[StudySession], completed_at: datetime, minimum: int) -> None:
    for session in sessions:
        session.completed_at = as_utc(completed_at)
        session.duration_minutes = _elapsed_minutes(session.started_at, completed_at, minimum)


@router.get("", response_model=list[StudyTaskRead])
def list_tasks(
    current_user: CurrentUser,
    db: DatabaseSession,
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[StudyTaskRead]:
    query = (
        select(StudyPlanTask)
        .options(joinedload(StudyPlanTask.topic).joinedload(Topic.subject))
        .where(StudyPlanTask.user_id == current_user.id)
    )
    if status_filter:
        query = query.where(StudyPlanTask.status == status_filter)
    tasks = db.scalars(
        query.order_by(StudyPlanTask.scheduled_for.is_(None), StudyPlanTask.scheduled_for, StudyPlanTask.id)
    ).all()
    return [_read(task) for task in tasks]


@router.post("", response_model=StudyTaskRead, status_code=status.HTTP_201_CREATED)
def create_task(payload: StudyTaskCreate, current_user: CurrentUser, db: DatabaseSession) -> StudyTaskRead:
    plan = owned_or_404(db, StudyPlan, payload.study_plan_id, current_user.id)
    if payload.topic_id is not None:
        topic = db.get(Topic, payload.topic_id)
        if topic is None or topic.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")
    values = payload.model_dump()
    values.pop("study_plan_id")
    if "order_index" not in payload.model_fields_set:
        last_order = db.scalar(
            select(func.max(StudyPlanTask.order_index)).where(StudyPlanTask.study_plan_id == plan.id)
        )
        values["order_index"] = (last_order + 1) if last_order is not None else 0
    values["scheduled_for"] = normalized_datetime(values["scheduled_for"])
    task = StudyPlanTask(user_id=current_user.id, study_plan_id=plan.id, **values)
    db.add(task)
    try:
        db.commit()
        db.refresh(task)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Study task already exists") from exc
    return _read(task)


@router.post("/{task_id}/start", response_model=StudySessionRead)
def start_task(task_id: int, current_user: CurrentUser, db: DatabaseSession) -> StudySessionRead:
    task = _owned_task_for_update(db, task_id, current_user.id)
    if task.status not in {"pending", "in_progress"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Study task cannot be started in its current status")

    existing_sessions = _open_sessions(db, current_user.id, task.id)
    if existing_sessions:
        task.status = "in_progress"
        task.completed_at = None
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Study task could not be started") from exc
        return _session_read(existing_sessions[0])

    started_at = utc_now()
    session = StudySession(user_id=current_user.id, task_id=task.id, started_at=started_at)
    task.status = "in_progress"
    task.completed_at = None
    db.add(session)
    try:
        db.commit()
        db.refresh(session)
    except IntegrityError as exc:
        db.rollback()
        task = _owned_task_for_update(db, task_id, current_user.id)
        existing_sessions = _open_sessions(db, current_user.id, task.id)
        if not existing_sessions:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Study task could not be started") from exc
        task.status = "in_progress"
        task.completed_at = None
        db.commit()
        return _session_read(existing_sessions[0])
    return _session_read(session)


@router.post("/{task_id}/complete", response_model=StudyTaskRead)
def complete_task(
    task_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    payload: StudyTaskCompleteRequest | None = Body(default=None),
) -> StudyTaskRead:
    task = _owned_task_for_update(db, task_id, current_user.id)
    if task.status == "cancelled":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cancelled study task cannot be completed")
    if task.status == "completed":
        open_sessions = _open_sessions(db, current_user.id, task.id)
        if open_sessions:
            completed_at = as_utc(task.completed_at or utc_now())
            _close_sessions(open_sessions, completed_at, 1)
            db.commit()
        return _read(task)
    if task.status not in {"pending", "in_progress"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Study task cannot be completed in its current status")

    completed_at = utc_now()
    notes = payload.notes if payload is not None else None
    open_sessions = _open_sessions(db, current_user.id, task.id)
    if open_sessions:
        _close_sessions(open_sessions, completed_at, 1)
        if notes is not None:
            for session in open_sessions:
                session.notes = notes
    else:
        session = StudySession(
            user_id=current_user.id,
            task_id=task.id,
            started_at=completed_at - timedelta(minutes=task.duration_minutes),
            completed_at=completed_at,
            duration_minutes=task.duration_minutes,
            notes=notes,
        )
        db.add(session)
    task.status = "completed"
    task.completed_at = completed_at
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Study task could not be completed") from exc
    return _read(task)


@router.post("/{task_id}/stop", response_model=StudyTaskRead)
def stop_task(task_id: int, current_user: CurrentUser, db: DatabaseSession) -> StudyTaskRead:
    task = _owned_task_for_update(db, task_id, current_user.id)
    if task.status not in {"pending", "in_progress"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Study task cannot be stopped in its current status")
    completed_at = utc_now()
    _close_sessions(_open_sessions(db, current_user.id, task.id), completed_at, 0)
    task.status = "pending"
    task.completed_at = None
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Study task could not be stopped") from exc
    return _read(task)


@router.get("/{task_id}", response_model=StudyTaskRead)
def get_task(task_id: int, current_user: CurrentUser, db: DatabaseSession) -> StudyTaskRead:
    return _read(owned_or_404(db, StudyPlanTask, task_id, current_user.id))


@router.patch("/{task_id}", response_model=StudyTaskRead)
def update_task(
    task_id: int, payload: StudyTaskUpdate, current_user: CurrentUser, db: DatabaseSession
) -> StudyTaskRead:
    task = owned_or_404(db, StudyPlanTask, task_id, current_user.id)
    values = payload.model_dump(exclude_unset=True)
    if "topic_id" in values and values["topic_id"] is not None:
        topic = db.get(Topic, values["topic_id"])
        if topic is None or topic.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")
    if "scheduled_for" in values:
        values["scheduled_for"] = normalized_datetime(values["scheduled_for"])
    if "status" in values and values["status"] not in {"pending", "in_progress", "completed", "cancelled"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported task status")
    for key, value in values.items():
        setattr(task, key, value)
    if values.get("status") == "completed" and task.completed_at is None:
        task.completed_at = utc_now()
    elif "status" in values and values["status"] != "completed":
        task.completed_at = None
    try:
        db.commit()
        db.refresh(task)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Study task already exists") from exc
    return _read(task)


@router.delete("/{task_id}", response_model=MessageResponse)
def delete_task(task_id: int, current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    task = owned_or_404(db, StudyPlanTask, task_id, current_user.id)
    db.delete(task)
    db.commit()
    return MessageResponse(message="Study task deleted")
