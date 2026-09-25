import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import DatabaseSession
from app.core.logging import log_event
from app.schemas.common import HealthResponse

router = APIRouter(tags=["health"])
logger = logging.getLogger("school_assistant.health")


def database_health(db: Session) -> HealthResponse:
    try:
        result = db.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError:
        db.rollback()
        log_event(logger, "health_database_unavailable")
        return HealthResponse(status="degraded", database="unavailable")
    if result != 1:
        return HealthResponse(status="degraded", database="unexpected")
    return HealthResponse(status="ok", database="ok")


@router.get("/health", response_model=HealthResponse)
def health(response: Response, db: DatabaseSession) -> HealthResponse:
    result = database_health(db)
    if result.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result


@router.get("/health/live", response_model=HealthResponse)
def liveness() -> HealthResponse:
    return HealthResponse(status="ok", database="not_checked")
