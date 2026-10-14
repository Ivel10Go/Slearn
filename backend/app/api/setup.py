from fastapi import APIRouter, HTTPException, status

from app.api.deps import AppSettings, DatabaseSession
from app.schemas.auth import UserRead
from app.schemas.setup import SetupRequest, SetupStatus
from app.services.setup import SetupAlreadyDoneError, create_first_user, needs_setup

router = APIRouter(prefix="/api/v1/setup", tags=["setup"])


def _require_local_mode(settings: AppSettings) -> None:
    """Die Einrichtung gibt es nur ohne Anmeldung.

    Mit Anmeldung entsteht das erste Konto aus ``ADMIN_USERNAME`` und
    ``ADMIN_PASSWORD``. Ein offener Endpunkt, der auf einem leeren Server das
    erste Admin-Konto an den Schnellsten vergibt, waere dort eine Luecke -
    deshalb 404 statt 403: der Endpunkt existiert auf so einem Server nicht.
    """
    if settings.auth_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


@router.get("", response_model=SetupStatus)
def get_setup_status(db: DatabaseSession, settings: AppSettings) -> SetupStatus:
    _require_local_mode(settings)
    return SetupStatus(needs_setup=needs_setup(db))


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def complete_setup(payload: SetupRequest, db: DatabaseSession, settings: AppSettings) -> UserRead:
    _require_local_mode(settings)
    try:
        user = create_first_user(db, payload.name, payload.timezone or settings.timezone)
    except SetupAlreadyDoneError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slearn ist bereits eingerichtet.") from exc
    return UserRead.model_validate(user)
