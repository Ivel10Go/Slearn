from fastapi import APIRouter

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.schemas.dashboard import DashboardResponse
from app.services.dashboard import serialize_dashboard

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> DashboardResponse:
    return serialize_dashboard(db, current_user.id, settings, user_timezone=current_user.timezone)
