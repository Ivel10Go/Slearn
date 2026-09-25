import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api import auth, calendar, dashboard, exams, health, homework, study, subjects
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, log_event
from app.db.entities import User
from app.db.session import dispose_engine, session_scope
from app.services.auth import ensure_bootstrap_user
from app.services.demo_seed import seed_demo_data_and_commit

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
    try:
        yield
    finally:
        dispose_engine()


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
    application.include_router(subjects.router)
    application.include_router(homework.router)
    application.include_router(exams.router)
    application.include_router(calendar.router)
    application.include_router(study.router)
    from app.api import integrations
    application.include_router(integrations.router)
    return application


app = create_app()
