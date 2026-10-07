from fastapi import APIRouter, HTTPException, status

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.core.time import resolve_timezone_name, zone_or_utc
from app.schemas.planner import PlannerRequest, PlannerResponse
from app.services import planner as planner_service
from app.services.planner import PlannerError

router = APIRouter(prefix="/api/v1/study/planner", tags=["study"])


@router.post("/preview", response_model=PlannerResponse)
def preview_plan(
    payload: PlannerRequest, current_user: CurrentUser, db: DatabaseSession, settings: AppSettings
) -> PlannerResponse:
    """Der Vorschlag fuer den Zeitraum. Schreibt nichts."""
    zone = zone_or_utc(resolve_timezone_name(current_user.timezone, settings.timezone))
    try:
        return planner_service.build_plan(db, current_user.id, zone, payload, apply=False)
    except PlannerError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    finally:
        # Die Vorschau laedt Aufgaben in die Sitzung; nichts davon darf haengen bleiben.
        db.rollback()


@router.post("/apply", response_model=PlannerResponse)
def apply_plan(
    payload: PlannerRequest, current_user: CurrentUser, db: DatabaseSession, settings: AppSettings
) -> PlannerResponse:
    """Rechnet den Vorschlag neu und traegt ihn ein: Termine setzen, neue Bloecke anlegen."""
    zone = zone_or_utc(resolve_timezone_name(current_user.timezone, settings.timezone))
    try:
        result = planner_service.build_plan(db, current_user.id, zone, payload, apply=True)
    except PlannerError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()
    return result
