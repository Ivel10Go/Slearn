import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api import (
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
    progress,
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
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.stop()
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
    from app.api import documents, files, integrations
    application.include_router(integrations.router)
    application.include_router(files.router)
    application.include_router(documents.router)
    return application


app = create_app()
