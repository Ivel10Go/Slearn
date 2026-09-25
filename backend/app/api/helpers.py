from datetime import datetime, timezone
from typing import Any, TypeVar

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.time import as_utc
from app.db.entities import Exam, Homework, Subject

ModelType = TypeVar("ModelType")


def owned_or_404(db: Session, model: type[ModelType], object_id: int, user_id: int) -> ModelType:
    instance = db.get(model, object_id)
    if instance is None or getattr(instance, "user_id", None) != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    return instance


def subject_or_404(db: Session, subject_id: int | None, user_id: int) -> Subject | None:
    if subject_id is None:
        return None
    subject = db.get(Subject, subject_id)
    if subject is None or subject.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subject not found")
    return subject


def normalized_datetime(value: datetime | None) -> datetime | None:
    return as_utc(value) if value is not None else None


def homework_dict(item: Homework) -> dict[str, Any]:
    return {
        "id": item.id,
        "subject_id": item.subject_id,
        "subject_name": item.subject.name if item.subject else None,
        "subject_color": item.subject.color if item.subject else None,
        "title": item.title,
        "description": item.description,
        "due_date": as_utc(item.due_date),
        "completed": item.completed,
        "completed_at": as_utc(item.completed_at) if item.completed_at else None,
        "source": item.source,
        "external_id": item.external_id,
    }


def exam_dict(item: Exam, now: datetime | None = None) -> dict[str, Any]:
    current = as_utc(now or datetime.now(timezone.utc))
    return {
        "id": item.id,
        "subject_id": item.subject_id,
        "subject_name": item.subject.name if item.subject else None,
        "subject_color": item.subject.color if item.subject else None,
        "title": item.title,
        "description": item.description,
        "date": as_utc(item.date),
        "days_until": max(0, (as_utc(item.date).date() - current.date()).days),
        "source": item.source,
        "external_id": item.external_id,
    }
