from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings, require_secret
from app.core.security import decode_session_token
from app.db.entities import User
from app.db.session import get_db
from app.services.model_downloads import ModelDownloadManager
from app.services.speech import SpeechService
from app.services.training_jobs import TrainingRunner


def get_app_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if settings is not None else get_settings()


def get_current_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> User:
    if not settings.auth_enabled:
        try:
            user = db.scalar(select(User).where(User.is_active.is_(True)).order_by(User.id).limit(1))
        except SQLAlchemyError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc
        if user is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No active user is configured")
        return user

    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        authorization = request.headers.get("Authorization", "")
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            token = value
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        claims = decode_session_token(token, require_secret(settings))
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        ) from exc
    if claims is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session")
    try:
        user = db.get(User, claims.user_id)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DatabaseSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]


def get_model_downloads(request: Request) -> ModelDownloadManager:
    """Der eine Download-Manager der Anwendung.

    Er haengt an ``app.state`` und wird in ``create_app`` **immer** angelegt -
    auch wenn kein Download laeuft. Ein ``None`` bedeutet daher einen
    Programmierfehler und keine Konfiguration, deshalb 503 mit Klartext statt
    eines zweiten Bauwegs, der den Pfad ein zweites Mal erfinden wuerde.

    Der Manager nimmt seinen Ablagepfad **einmal** entgegen. Ein Test, der die
    Ablage umbiegen will, setzt deshalb ein neues Exemplar auf ``app.state`` -
    so wie ``test_backend.py`` es mit ``app.state.settings`` macht.
    """
    manager: ModelDownloadManager | None = getattr(request.app.state, "model_downloads", None)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Der Modelldownload ist nicht eingerichtet",
        )
    return manager


ModelDownloads = Annotated[ModelDownloadManager, Depends(get_model_downloads)]


def get_speech(request: Request) -> SpeechService:
    """Der eine Sprachdienst der Anwendung.

    Er haengt an ``app.state`` und wird in ``create_app`` **immer** gebaut -
    auch dann, wenn kein Modell und keine Laufzeit da sind. Gerade das ist der
    Normalzustand: der Dienst muss antworten koennen, *um zu sagen*, dass er
    nichts kann. Ein ``None`` bedeutet daher einen Programmierfehler, und 503
    mit Klartext ist hier die ehrliche Antwort darauf.

    Er teilt sich den Download-Manager mit ``/api/v1/models``: Modelle und
    Stimmen liegen im selben Verzeichnis, und zwei Manager wuerden zwei Sperren
    und zwei Antworten auf dieselbe Frage erzeugen.
    """
    service: SpeechService | None = getattr(request.app.state, "speech", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Der Sprachdienst ist nicht eingerichtet",
        )
    return service


#: Der Alias heisst ``Speech`` und nicht ``SpeechService``, weil der Klassenname
#: schon vergeben ist - derselbe Trick wie bei ``CurrentUser``/``User``. In einer
#: Signatur liest sich ``speech: Speech`` besser als ``speech: SpeechServiceDep``.
Speech = Annotated[SpeechService, Depends(get_speech)]


def get_training_runner(request: Request) -> TrainingRunner:
    """Die Trainingsrunden - wie der Sprachdienst immer gebaut, ``None`` ist ein Programmierfehler."""
    runner: TrainingRunner | None = getattr(request.app.state, "training_runner", None)
    if runner is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Das Training ist nicht eingerichtet",
        )
    return runner


TrainingJobs = Annotated[TrainingRunner, Depends(get_training_runner)]
