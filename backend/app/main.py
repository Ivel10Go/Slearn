import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api import (
    ai_runtime,
    auth,
    backups,
    calendar,
    dashboard,
    exams,
    grades,
    greeting,
    health,
    homework,
    lessons,
local_models,
    notifications,
    progress,
rewards,
speech,
study,
    subjects,
)
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, log_event
from app.db.entities import User
from app.db.session import dispose_engine, session_scope
from app.services.auth import ensure_bootstrap_user
from app.services.demo_seed import seed_demo_data_and_commit
from app.services.integration_scheduler import IntegrationSyncScheduler
from app.services.model_downloads import ModelDownloadManager
from app.services.notification_scheduler import ReminderScheduler
from app.services.speech import PiperSynthesizer, SpeechService, WhisperTranscriber

logger = logging.getLogger("school_assistant")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings: Settings = application.state.settings
    configure_logging(settings.log_level)
    try:
        with session_scope(settings) as db:
            bootstrap_user = ensure_bootstrap_user(db, settings) if settings.bootstrap_on_startup else None
            if settings.demo_mode:
                user = bootstrap_user or db.scalar(
                    select(User).where(User.is_active.is_(True)).order_by(User.id).limit(1)
                )
                if user is not None:
                    result = seed_demo_data_and_commit(db, user.id)
                    log_event(
                        logger,
                        "demo_seed_completed",
                        created=result.created,
                        updated=result.updated,
                        total=result.total,
                    )
    except (SQLAlchemyError, RuntimeError, ValueError):
        log_event(logger, "startup_database_task_failed")
    scheduler = _start_scheduler(application, settings)
    downloads: ModelDownloadManager | None = getattr(application.state, "model_downloads", None)
    reminders = _start_reminder_scheduler(application, settings)
    try:
        yield
    finally:
        if reminders is not None:
            reminders.stop()
        if scheduler is not None:
            scheduler.stop()
        if downloads is not None:
            # Laedende Threads beenden und die Teilstaende liegen lassen: ein
            # abgebrochener Download ist ein Zustand, aus dem der naechste Start
            # sauber fortsetzt - kein halbes Modell.
            downloads.shutdown()
        dispose_engine()


def _start_scheduler(application: FastAPI, settings: Settings) -> IntegrationSyncScheduler | None:
    """Startet den planmäßigen Integrationsabruf, falls er eingeschaltet ist.

    Das Attribut ``integration_scheduler`` gibt es immer, auch wenn kein Thread
    läuft: Tests und der Status-Endpunkt müssen nicht erst prüfen, ob die
    Einstellung existiert.
    """
    if not settings.webuntis_sync_enabled:
        application.state.integration_scheduler = None
        log_event(logger, "startup_scheduler_disabled")
        return None
    scheduler = IntegrationSyncScheduler(settings)
    application.state.integration_scheduler = scheduler
    scheduler.start()
    log_event(logger, "startup_scheduler_enabled", interval_seconds=scheduler.interval_seconds)
    return scheduler


def _start_reminder_scheduler(application: FastAPI, settings: Settings) -> ReminderScheduler | None:
    """Startet den Erinnerungslauf, falls er eingeschaltet ist.

    Ein **eigener** Thread, nicht der WebUntis-Thread: andere Taktart, andere
    Fehlergrenze, und der WebUntis-Thread laeuft ueberhaupt nur mit
    ``WEBUNTIS_SYNC_ENABLED``. Die Begruendung steht im Modul-Docstring von
    ``app/services/notification_scheduler.py``.

    Das Attribut ``reminder_scheduler`` gibt es wie das andere immer, auch als
    ``None`` - die Einstellungen zeigen damit an, was auf diesem Server laeuft,
    ohne dass sie die Einstellung selbst erraten muessen.
    """
    if not settings.reminder_scheduler_enabled:
        application.state.reminder_scheduler = None
        log_event(logger, "startup_reminder_scheduler_disabled")
        return None
    scheduler = ReminderScheduler(settings)
    application.state.reminder_scheduler = scheduler
    scheduler.start()
    log_event(logger, "startup_reminder_scheduler_enabled", interval_seconds=scheduler.interval_seconds)
    return scheduler


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    application = FastAPI(
        title=active_settings.app_name,
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    application.state.settings = active_settings
    # Der Download-Manager entsteht schon hier und nicht im Lifespan: Er macht
    # beim Bauen keinen Dateizugriff, und die Endpunkte brauchen ihn auch dann,
    # wenn der Lifespan noch nicht gelaufen ist (etwa in einem Test ohne
    # TestClient). `application.state.model_downloads` gibt es deshalb immer.
    downloads = ModelDownloadManager(
        Path(active_settings.model_storage_path),
        timeout_seconds=active_settings.model_download_timeout_seconds,
        min_free_bytes=active_settings.model_min_free_bytes,
    )
    application.state.model_downloads = downloads
    # Der Sprachdienst **immer**, auch ohne Modell und ohne Laufzeit. Das ist der
    # Normalzustand auf einem frischen Server, und genau dann muss
    # `GET /api/v1/speech/capabilities` koennen sagen, dass nichts geladen ist.
    # Ein `None` wuerde genau diese Antwort unterdruecken.
    #
    # Er teilt sich `downloads` mit `/api/v1/models`: Modelle und Stimmen liegen
    # im selben Verzeichnis, und ein zweiter Manager ergaebe zwei Sperren und
    # zwei Staende fuer dieselbe Frage.
    application.state.speech = SpeechService(
        downloads=downloads,
        transcriber=(
            WhisperTranscriber(
                active_settings.whisper_server_url,
                language=active_settings.whisper_language,
                timeout_seconds=active_settings.whisper_server_timeout_seconds,
            )
            if active_settings.whisper_server_url
            else None
        ),
        synthesizer=(
            PiperSynthesizer(active_settings.piper_binary_path)
            if active_settings.piper_binary_path
            else None
        ),
        enabled=active_settings.speech_enabled,
        stt_model_id=active_settings.speech_stt_model_id,
        voice_id=active_settings.speech_tts_voice_id,
        max_upload_bytes=active_settings.speech_max_upload_bytes,
        max_seconds=active_settings.speech_max_seconds,
        max_text_characters=active_settings.speech_max_text_characters,
        ffmpeg_path=active_settings.ffmpeg_binary_path,
    )
    if active_settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=active_settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    application.include_router(health.router)
    application.include_router(auth.router)
    application.include_router(dashboard.router)
    application.include_router(lessons.router)
    application.include_router(greeting.router)
    application.include_router(subjects.router)
    application.include_router(homework.router)
    application.include_router(exams.router)
    application.include_router(calendar.router)
    # Drei Router aus einem Modul: Plaene, Serien und Aufgaben teilen sich
    # die Routen, aber nicht das Praefix. Ohne alle drei sind die
    # Abhaengigkeiten und der Aufgaben-Generator nicht erreichbar.
    application.include_router(study.plans_router)
    application.include_router(study.series_router)
    application.include_router(study.router)
    # Lernfortschritt und Lernprotokoll. Ohne diese Zeile waeren die
    # Fortschrittskarte und der erste Ort, an dem `LearningProgress`
    # geschrieben wird, nicht erreichbar.
    application.include_router(progress.router)
    # Noten. Ohne diese Zeile waeren Schnitt, Verlauf, Hochrechnung und die
    # Freigabe fuer den KI-Dienst nicht erreichbar.
    application.include_router(grades.router)
    # Der Backup-Status. Ohne diese Zeile waere der read-only Endpunkt nicht
    # erreichbar und die Einstellungen zeigten dauerhaft "nicht verfuegbar".
    application.include_router(backups.router)
    application.include_router(local_models.router)
    # Der Sprach-Assistent: Faehigkeiten, Stimmen, Transkript und Ton. Ohne
    # diese Zeile waere `GET /api/v1/speech/capabilities` nicht erreichbar und
    # die Oberflaeche wuerde gar nicht erst erfahren, dass der Textmodus der
    # Normalzustand ist.
    application.include_router(speech.router)
    # Die bekannten lokalen KI-Laufzeiten mit ihren Standardadressen und dem
    # `host.docker.internal`-Hinweis. Ohne diese Zeile zeigte das Formular fuer
    # einen KI-Server keine Vorschlaege und keinen Hinweis auf den haeufigsten
    # Konfigurationsfehler.
    application.include_router(ai_runtime.router)
    # Benachrichtigungen: die Erinnerungseinstellungen, der oeffentliche
    # VAPID-Schluessel, die Abonnements und der Weg, ueber den die offene Seite
    # ihre Erinnerungen abholt. Ohne diese Zeile waere der ganze Abschnitt
    # unerreichbar - und die Einstellungen zeigten einen leeren Dialog.
    application.include_router(notifications.router)
    # Punkte und Auszeichnungen. Ohne diese Zeile waere die Belohnungskarte nicht
    # erreichbar, und die Punkte, die die Aufgaben-Endpunkte vergeben, haetten
    # keinen Ort, an dem sie sichtbar werden.
    application.include_router(rewards.router)
    from app.api import documents, files, integrations
    application.include_router(integrations.router)
    application.include_router(files.router)
    application.include_router(documents.router)
    return application


app = create_app()
