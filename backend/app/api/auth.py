import hashlib
import logging
from threading import Lock

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.core.config import require_secret
from app.core.logging import log_event
from app.core.rate_limit import SlidingWindowRateLimiter
from app.core.security import create_session_token
from app.schemas.auth import LoginRequest, LoginResponse, UserRead
from app.schemas.common import MessageResponse
from app.services.auth import authenticate_user

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
logger = logging.getLogger("school_assistant.auth")
_limiters: dict[tuple[int, int], SlidingWindowRateLimiter] = {}
_limiter_lock = Lock()


def _get_limiter(settings: AppSettings) -> SlidingWindowRateLimiter:
    key = (settings.login_rate_limit, settings.login_rate_window_seconds)
    with _limiter_lock:
        limiter = _limiters.get(key)
        if limiter is None:
            limiter = SlidingWindowRateLimiter(*key)
            _limiters[key] = limiter
        return limiter


def _rate_limit_key(request: Request, username: str) -> str:
    client_host = request.client.host if request.client else "unknown"
    username_hash = hashlib.sha256(username.strip().lower().encode("utf-8")).hexdigest()
    return f"{client_host}:{username_hash}"


def _set_session_cookie(response: Response, token: str, settings: AppSettings) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_max_age_seconds,
        expires=settings.session_max_age_seconds,
        path="/",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=settings.cookie_httponly,
        samesite=settings.cookie_samesite,
    )


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DatabaseSession,
    settings: AppSettings,
) -> LoginResponse:
    limiter = _get_limiter(settings)
    limiter_key = _rate_limit_key(request, payload.username)
    allowed, retry_after = limiter.allow(limiter_key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        user = authenticate_user(db, payload.username, payload.password)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc
    if user is None:
        log_event(logger, "login_failed")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    try:
        token = create_session_token(user.id, require_secret(settings), settings.session_max_age_seconds)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        ) from exc
    limiter.reset(limiter_key)
    _set_session_cookie(response, token, settings)
    log_event(logger, "login_succeeded", user_id=user.id)
    return LoginResponse(user=UserRead.model_validate(user))


@router.post("/logout", response_model=MessageResponse)
def logout(response: Response, settings: AppSettings) -> MessageResponse:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=settings.cookie_httponly,
        samesite=settings.cookie_samesite,
    )
    return MessageResponse(message="Logged out")


@router.get("/me", response_model=UserRead)
def me(current_user: CurrentUser) -> UserRead:
    return UserRead.model_validate(current_user)
