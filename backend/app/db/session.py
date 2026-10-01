from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings, normalize_database_url

_engine: Engine | None = None
_engine_url: str | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine(settings: Settings | None = None) -> Engine:
    global _engine, _engine_url, _session_factory
    active_settings = settings or get_settings()
    url = normalize_database_url(active_settings.database_url)
    if _engine is not None and _engine_url == url:
        return _engine
    if _engine is not None:
        _engine.dispose()
    options: dict[str, Any] = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    else:
        options.update({"pool_size": 5, "max_overflow": 10, "pool_recycle": 1800})
    _engine = create_engine(url, **options)
    if url.startswith("sqlite"):
        event.listen(_engine, "connect", _configure_sqlite_connection)
    _engine_url = url
    _session_factory = sessionmaker(bind=_engine, class_=Session, autoflush=False, expire_on_commit=False)
    return _engine


def _configure_sqlite_connection(dbapi_connection: Any, _record: Any) -> None:
    """SQLite so einstellen, dass es sich wie der Postgres-Betrieb verhaelt.

    - ``foreign_keys``: SQLite prueft Fremdschluessel nur auf Nachfrage.
      Postgres prueft sie immer - ohne das Pragma liesse die Desktop-App
      Verweise ins Leere zu, die der Server mit einem Fehler ablehnt, und
      ``ON DELETE CASCADE`` liefe gar nicht.
    - ``journal_mode=WAL``: Lesen blockiert das Schreiben nicht. Der
      Erinnerungs- und der WebUntis-Thread schreiben neben den Anfragen.
    - ``busy_timeout``: eine kurz gesperrte Datenbank wartet, statt sofort mit
      ``database is locked`` abzubrechen.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    get_engine(settings)
    if _session_factory is None:
        raise RuntimeError("Database session factory is not initialized")
    return _session_factory


def get_db(request: Request) -> Generator[Session, None, None]:
    settings = getattr(request.app.state, "settings", None)
    session = get_session_factory(settings)()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope(settings: Settings | None = None) -> Generator[Session, None, None]:
    session = get_session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_engine() -> None:
    global _engine, _engine_url, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _engine_url = None
    _session_factory = None
