"""Datenauskunft und Kontoloeschung - Logik in ``app/services/account.py``."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.core.security import verify_password
from app.core.time import utc_now
from app.schemas.account import AccountDeleteRequest, AccountDeleteResponse
from app.services import account
from app.services.training_jobs import TrainingRunner

router = APIRouter(prefix="/api/v1/account", tags=["account"])


@router.get("/export")
def export_account(current_user: CurrentUser, db: DatabaseSession, settings: AppSettings) -> Response:
    """Alle eigenen Daten als ZIP (``daten.json`` + hochgeladene Dateien)."""
    try:
        content = account.build_export(db, current_user, Path(settings.document_storage_path))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc
    filename = f"lernraum-daten-{utc_now():%Y-%m-%d}.zip"
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.post("/delete", response_model=AccountDeleteResponse)
def delete_account(
    payload: AccountDeleteRequest,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> AccountDeleteResponse:
    """Loescht das eigene Konto unwiderruflich.

    Mit Anmeldung verlangt der Aufruf zusaetzlich das Passwort: ein offenes
    Browserfenster allein soll kein Konto loeschen koennen.
    """
    if payload.confirm.strip() != account.DELETE_CONFIRMATION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Zur Bestätigung bitte „{account.DELETE_CONFIRMATION}“ eingeben.",
        )
    if settings.auth_enabled and not verify_password(payload.password or "", current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Das Passwort stimmt nicht.")
    runner: TrainingRunner | None = getattr(request.app.state, "training_runner", None)
    if runner is not None:
        runner.forget(current_user.id)
    try:
        result = account.delete_user_data(db, current_user, Path(settings.document_storage_path))
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=settings.cookie_httponly,
        samesite=settings.cookie_samesite,
    )
    return AccountDeleteResponse(deleted_rows=result.rows, deleted_files=result.files)
