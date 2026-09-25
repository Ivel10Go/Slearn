import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, require_admin_credentials
from app.core.logging import log_event
from app.core.security import hash_password, verify_password
from app.core.time import as_utc, utc_now
from app.db.entities import User, UserSettings

logger = logging.getLogger("school_assistant.auth")


def ensure_bootstrap_user(db: Session, settings: Settings) -> User | None:
    if not settings.bootstrap_on_startup:
        return None
    username, password = require_admin_credentials(settings)
    user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    changed = False
    if user is None:
        user = User(
            username=username,
            password_hash=hash_password(password),
            is_admin=True,
            is_active=True,
            timezone=settings.timezone,
        )
        db.add(user)
        changed = True
    else:
        if not user.is_admin:
            user.is_admin = True
            changed = True
        if not user.is_active:
            user.is_active = True
            changed = True
    db.flush()
    user_settings = db.scalar(select(UserSettings).where(UserSettings.user_id == user.id))
    if user_settings is None:
        db.add(UserSettings(user_id=user.id, timezone=settings.timezone))
        changed = True
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if changed:
        log_event(logger, "bootstrap_user_ready", user_id=user.id)
    return user


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(
        select(User).where(func.lower(User.username) == username.strip().lower(), User.is_active.is_(True))
    )
    if user is None or not verify_password(password, user.password_hash):
        return None
    user.last_login_at = utc_now()
    db.commit()
    db.refresh(user)
    return user


def user_to_dict(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "is_active": user.is_active,
        "is_admin": user.is_admin,
        "timezone": user.timezone,
        "created_at": as_utc(user.created_at),
        "last_login_at": as_utc(user.last_login_at) if user.last_login_at else None,
    }
