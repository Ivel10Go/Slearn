from datetime import date

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.core.time import resolve_timezone_name, utc_now, zone_or_utc
from app.schemas.lessons import LessonRangeResponse
from app.services.lessons import LessonRangeError, serialize_lessons

router = APIRouter(prefix="/api/v1/lessons", tags=["lessons"])


@router.get("", response_model=LessonRangeResponse)
def list_lessons(
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
) -> LessonRangeResponse:
    """Unterrichtsstunden eines Zeitraums, standardmäßig für heute.

    ``start`` und ``end`` sind lokale Kalendertage in der Zeitzone des Benutzers
    und beide **einschließlich**. Fehlt einer, beginnt bzw. endet der Zeitraum am
    heutigen Tag. ``end`` vor ``start`` ist ein Fehler und keine Korrektur.
    """
    timezone_name = resolve_timezone_name(current_user.timezone or settings.timezone, settings.timezone)
    local_today = utc_now().astimezone(zone_or_utc(timezone_name)).date()
    first = start or local_today
    last = end or first
    try:
        return serialize_lessons(
            db,
            current_user.id,
            settings,
            first,
            last,
            user_timezone=current_user.timezone,
        )
    except LessonRangeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
