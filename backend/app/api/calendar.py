from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DatabaseSession
from app.api.helpers import normalized_datetime, owned_or_404
from app.db.entities import CalendarEvent
from app.schemas.academic import (
    CalendarEventCreate,
    CalendarEventRead,
    CalendarEventUpdate,
)
from app.schemas.common import MessageResponse

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])


@router.get("/events", response_model=list[CalendarEventRead])
@router.get("", response_model=list[CalendarEventRead])
def list_events(
    current_user: CurrentUser,
    db: DatabaseSession,
    upcoming: bool = Query(default=True),
) -> list[CalendarEvent]:
    query = select(CalendarEvent).where(CalendarEvent.user_id == current_user.id)
    if upcoming:
        from app.core.time import utc_now

        query = query.where(CalendarEvent.end_time >= utc_now())
    return list(db.scalars(query.order_by(CalendarEvent.start_time)).all())


@router.post("/events", response_model=CalendarEventRead, status_code=status.HTTP_201_CREATED)
@router.post("", response_model=CalendarEventRead, status_code=status.HTTP_201_CREATED)
def create_event(payload: CalendarEventCreate, current_user: CurrentUser, db: DatabaseSession) -> CalendarEvent:
    values = payload.model_dump()
    values["start_time"] = normalized_datetime(values["start_time"])
    values["end_time"] = normalized_datetime(values["end_time"])
    if values["end_time"] < values["start_time"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="end_time must not precede start_time"
        )
    item = CalendarEvent(user_id=current_user.id, **values)
    db.add(item)
    try:
        db.commit()
        db.refresh(item)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Calendar event already exists")
    return item


@router.get("/events/{event_id}", response_model=CalendarEventRead)
def get_event(event_id: int, current_user: CurrentUser, db: DatabaseSession) -> CalendarEvent:
    return owned_or_404(db, CalendarEvent, event_id, current_user.id)


@router.patch("/events/{event_id}", response_model=CalendarEventRead)
def update_event(
    event_id: int, payload: CalendarEventUpdate, current_user: CurrentUser, db: DatabaseSession
) -> CalendarEvent:
    item = owned_or_404(db, CalendarEvent, event_id, current_user.id)
    values = payload.model_dump(exclude_unset=True)
    for key in ("start_time", "end_time"):
        if key in values:
            values[key] = normalized_datetime(values[key])
    start = normalized_datetime(values.get("start_time", item.start_time))
    end = normalized_datetime(values.get("end_time", item.end_time))
    if start is not None and end is not None and end < start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="end_time must not precede start_time"
        )
    for key, value in values.items():
        setattr(item, key, value)
    try:
        db.commit()
        db.refresh(item)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Calendar event already exists")
    return item


@router.delete("/events/{event_id}", response_model=MessageResponse)
def delete_event(event_id: int, current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    item = owned_or_404(db, CalendarEvent, event_id, current_user.id)
    db.delete(item)
    db.commit()
    return MessageResponse(message="Calendar event deleted")
