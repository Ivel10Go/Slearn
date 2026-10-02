import html
import logging

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.api.helpers import owned_or_404
from app.core import oauth_state
from app.core.config import require_integration_encryption_key, require_secret
from app.core.crypto import CredentialDecryptionError, decrypt_credentials, encrypt_credentials
from app.core.time import utc_now
from app.db.entities import SyncSource
from app.schemas.common import MessageResponse
from app.schemas.integrations import (
    IntegrationAuthorizeRead,
    IntegrationCreate,
    IntegrationRead,
    IntegrationSyncResult,
    IntegrationTestResult,
    IntegrationUpdate,
)
from app.services import calendar_consent, google_calendar
from app.services.calendar_sync import CalendarSyncError
from app.services.integrations import (
    INTEGRATION_KINDS,
    INTEGRATION_SPECS,
    IntegrationConnectionError,
    IntegrationValidationError,
    check_integration_connection,
    integration_read,
    missing_fields,
    normalize_config,
    normalize_credentials,
    run_integration_sync,
)

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])

logger = logging.getLogger("school_assistant.integrations")


def _encryption_key(settings: AppSettings) -> str:
    try:
        return require_integration_encryption_key(settings)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Integration encryption is not configured",
        ) from exc


def _read(source: SyncSource, settings: AppSettings) -> IntegrationRead:
    try:
        return integration_read(source, _encryption_key(settings))
    except CredentialDecryptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stored integration credentials cannot be decrypted with the configured key",
        ) from exc


def _sync_after_connect(
    db: Session, source: SyncSource, settings: AppSettings, timezone_name: str | None
) -> IntegrationRead:
    """Holt den Stundenplan sofort, sobald WebUntis verbunden und aktiv ist.

    Ohne diesen Schritt stuenden Faecher und Stunden erst nach dem ersten
    Knopfdruck oder dem naechsten Tageslauf da. Der Abruf ist best effort: ein
    Fehler landet in ``last_error`` (so macht es ``run_integration_sync``
    selbst) und verhindert nie das Speichern der Zugangsdaten. Nur WebUntis,
    nur wenn alle Pflichtangaben da sind.
    """
    integration = _read(source, settings)
    if source.source_type != "webuntis" or not source.enabled or integration.missing_fields:
        return integration
    try:
        run_integration_sync(
            db=db,
            source=source,
            config=integration.config,
            credential_keys=integration.credential_keys,
            settings=settings,
            timezone_name=timezone_name or settings.timezone,
        )
    except (RuntimeError, SQLAlchemyError):
        db.rollback()
        logger.warning("Sofortabruf nach dem Verbinden fehlgeschlagen", exc_info=True)
    db.refresh(source)
    return _read(source, settings)


def _owned_integration(db: Session, integration_id: int, user_id: int) -> SyncSource:
    source = owned_or_404(db, SyncSource, integration_id, user_id)
    if source.source_type not in INTEGRATION_KINDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    return source


@router.get("", response_model=list[IntegrationRead])
def list_integrations(current_user: CurrentUser, db: DatabaseSession, settings: AppSettings) -> list[IntegrationRead]:
    sources = db.scalars(
        select(SyncSource)
        .where(SyncSource.user_id == current_user.id, SyncSource.source_type.in_(INTEGRATION_KINDS))
        .order_by(SyncSource.source_type, SyncSource.name, SyncSource.id)
    ).all()
    return [_read(source, settings) for source in sources]


@router.get("/catalog", response_model=list[dict[str, object]])
def integration_catalog(settings: AppSettings) -> list[dict[str, object]]:
    return [
        {
            "kind": spec.kind,
            "label": spec.label,
            "description": spec.description,
            "required_config": list(spec.required_config),
            "optional_config": list(spec.optional_config),
            "required_one_of": [list(group) for group in spec.required_one_of],
            "required_credentials": list(spec.required_credentials),
            "optional_credentials": list(spec.optional_credentials),
            "supports_writing": spec.supports_writing,
        }
        for spec in INTEGRATION_SPECS.values()
    ]


@router.post("", response_model=IntegrationRead, status_code=status.HTTP_201_CREATED)
def create_integration(
    payload: IntegrationCreate,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> IntegrationRead:
    key = _encryption_key(settings)
    try:
        config = normalize_config(payload.kind, payload.config, settings.document_storage_path)
        credentials = normalize_credentials(payload.kind, payload.credentials)
    except IntegrationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    gaps = missing_fields(payload.kind, config, sorted(credentials))
    if gaps:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing required values: {', '.join(gaps)}",
        )

    source = SyncSource(
        user_id=current_user.id,
        source_type=payload.kind,
        name=payload.name,
        enabled=payload.enabled,
        configuration=config,
        credentials=encrypt_credentials(credentials, key) if credentials else None,
        status="configured",
    )
    db.add(source)
    try:
        db.commit()
        db.refresh(source)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An integration with this name already exists",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc
    return _sync_after_connect(db, source, settings, current_user.timezone)


@router.get("/{integration_id}", response_model=IntegrationRead)
def get_integration(
    integration_id: int, current_user: CurrentUser, db: DatabaseSession, settings: AppSettings
) -> IntegrationRead:
    return _read(_owned_integration(db, integration_id, current_user.id), settings)


@router.patch("/{integration_id}", response_model=IntegrationRead)
def update_integration(
    integration_id: int,
    payload: IntegrationUpdate,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> IntegrationRead:
    key = _encryption_key(settings)
    source = _owned_integration(db, integration_id, current_user.id)
    values = payload.model_dump(exclude_unset=True)

    if "name" in values and values["name"] is not None:
        source.name = values["name"]

    if "config" in values and values["config"] is not None:
        merged = {**{str(k): str(v) for k, v in (source.configuration or {}).items()}, **values["config"]}
        try:
            source.configuration = normalize_config(source.source_type, merged, settings.document_storage_path)
        except IntegrationValidationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if "credentials" in values and values["credentials"] is not None:
        try:
            credentials = normalize_credentials(source.source_type, values["credentials"])
        except IntegrationValidationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        if credentials:
            source.credentials = encrypt_credentials(credentials, key)
        else:
            source.credentials = None
        source.status = "configured"
        source.last_error = None

    if "enabled" in values and values["enabled"] is not None:
        source.enabled = values["enabled"]

    try:
        db.commit()
        db.refresh(source)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An integration with this name already exists",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc
    if "credentials" in values or "enabled" in values:
        return _sync_after_connect(db, source, settings, current_user.timezone)
    return _read(source, settings)


@router.delete("/{integration_id}", response_model=MessageResponse)
def delete_integration(integration_id: int, current_user: CurrentUser, db: DatabaseSession) -> MessageResponse:
    source = _owned_integration(db, integration_id, current_user.id)
    db.delete(source)
    db.commit()
    return MessageResponse(message="Integration disconnected")


@router.post("/{integration_id}/test", response_model=IntegrationTestResult)
def test_integration(
    integration_id: int, current_user: CurrentUser, db: DatabaseSession, settings: AppSettings
) -> IntegrationTestResult:
    source = _owned_integration(db, integration_id, current_user.id)
    integration = _read(source, settings)
    source.last_tested_at = utc_now()

    if integration.missing_fields:
        source.status = "error"
        source.last_error = f"Fehlende Angaben: {', '.join(integration.missing_fields)}"
        db.commit()
        return IntegrationTestResult(
            ok=False,
            status="error",
            message=source.last_error,
            missing_fields=integration.missing_fields,
        )

    label = INTEGRATION_SPECS[source.source_type].label
    if not source.enabled:
        source.status = "disabled"
        source.last_error = None
        db.commit()
        return IntegrationTestResult(
            ok=False,
            status="disabled",
            message=f"{label} ist deaktiviert, es wurde keine Verbindung getestet.",
            missing_fields=[],
        )

    key = _encryption_key(settings)
    credentials = decrypt_credentials(source.credentials, key) if source.credentials else {}
    try:
        message = check_integration_connection(
            kind=source.source_type,
            config=integration.config,
            credentials=credentials,
            document_storage_path=settings.document_storage_path,
            timeout_seconds=settings.integration_timeout_seconds,
            timezone_name=current_user.timezone or settings.timezone,
        )
    except IntegrationConnectionError as exc:
        source.status = "error"
        source.last_error = str(exc)
        db.commit()
        return IntegrationTestResult(ok=False, status="error", message=str(exc), missing_fields=[])

    source.status = "connected"
    source.last_error = None
    db.commit()
    return IntegrationTestResult(ok=True, status="connected", message=message, missing_fields=[])


@router.post("/{integration_id}/sync", response_model=IntegrationSyncResult)
def sync_integration_endpoint(
    integration_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> IntegrationSyncResult:
    """Holt Daten aus der Integration und speichert sie lokal.

    Der Abruf dauert mehrere Sekunden und braucht eine bestehende Sitzung beim
    Schulportal, deshalb holt ihn die Oberfläche über diesen Knopf. Ein
    planmäßiger Lauf im Hintergrund teilt sich denselben Ablauf in
    ``run_integration_sync``, beachtet aber zusätzlich ``last_sync_at``.
    Ein Klick hier bleibt davon unberührt: der Knopf holt immer, auch wenn der
    planmäßige Lauf heute schon gelaufen ist.
    """
    source = _owned_integration(db, integration_id, current_user.id)
    integration = _read(source, settings)

    try:
        run = run_integration_sync(
            db=db,
            source=source,
            config=integration.config,
            credential_keys=integration.credential_keys,
            settings=settings,
            timezone_name=current_user.timezone or settings.timezone,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Integration encryption is not configured",
        ) from exc
    except SQLAlchemyError:
        db.rollback()
        raise

    if not run.ok:
        return IntegrationSyncResult(
            ok=False,
            status=run.status,
            message=run.message,
            missing_fields=list(run.missing_fields),
        )

    outcome = run.outcome
    message = run.message
    if getattr(outcome, "imported_lessons", 0):
        skipped = getattr(outcome, "skipped_entries", 0)
        if skipped:
            message += f" {skipped} unvollständige Einträge wurden übersprungen."
    return IntegrationSyncResult(
        ok=True,
        status=run.status,
        message=message,
        imported_lessons=getattr(outcome, "imported_lessons", 0),
        subjects=getattr(outcome, "subjects", 0),
        teachers=getattr(outcome, "teachers", 0),
        rooms=getattr(outcome, "rooms", 0),
        skipped_entries=getattr(outcome, "skipped_entries", 0),
        imported_documents=getattr(outcome, "imported_documents", 0),
        updated_documents=getattr(outcome, "updated_documents", 0),
        indexed_chunks=getattr(outcome, "indexed_chunks", 0),
        removed_documents=getattr(outcome, "removed_documents", 0),
        skipped_files=getattr(outcome, "skipped_files", 0),
        imported_events=getattr(outcome, "imported_events", 0),
        updated_events=getattr(outcome, "updated_events", 0),
        pushed_events=getattr(outcome, "pushed_events", 0),
        deleted_events=getattr(outcome, "deleted_events", 0),
        removed_events=getattr(outcome, "removed_events", 0),
        conflicts=getattr(outcome, "conflicts", 0),
        floating_events=getattr(outcome, "floating_events", 0),
        window_start=getattr(outcome, "window_start", None),
        window_end=getattr(outcome, "window_end", None),
        last_sync_at=source.last_sync_at,
    )


# -- Google Kalender: Einwilligung und Rückkehr ----------------------------
#
# Der Rückweg zeigt bewusst auf **diese** Routen und nicht auf die Oberfläche.
# Ein Autorisierungscode im Frontend liefe durch Anwendungszustand und Browser-
# Verlauf; hier ist er ein einmaliger Parameter, der sofort gegen ein Token
# eingetauscht und danach verworfen wird.
#
# Die Reihenfolge im Rückweg ist nicht verhandelbar: ``state`` **vor** dem
# Tausch. Ein Code, der zu einer fremden Autorisierung gehört, darf den Server
# nicht einmal erreichen.
#
# Der Rückweg liefert HTML und keine Weiterleitung. Grund ist der Proxy:
# ``lib/proxy.ts`` verwirft den ``location``-Kopf jeder Antwort, also käme ein
# ``307`` ohne Ziel beim Browser an. Stattdessen bekommt der Nutzer eine Seite
# mit einem Link zurück auf ``/settings`` - und weil der Aufruf über denselben
# Ursprung läuft, stimmt der relative Pfad ohne jede Konfiguration.


def _google_integration(db: Session, integration_id: int, user_id: int) -> SyncSource:
    source = _owned_integration(db, integration_id, user_id)
    if source.source_type != "google_calendar":
        # 404 statt 400: eine fremde Art zu autorisieren würde sonst verraten,
        # dass die Id überhaupt existiert.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    return source


def _signing_secret(settings: AppSettings) -> str:
    try:
        return require_secret(settings)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Application signing is not configured",
        ) from exc


@router.post("/{integration_id}/authorize", response_model=IntegrationAuthorizeRead)
def authorize_integration(
    integration_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> IntegrationAuthorizeRead:
    """Bereitet die Einwilligung vor und gibt die Adresse für den Browser zurück.

    Der Server leitet **nicht** selbst um. Die Oberfläche soll vorher noch
    prüfen können, ob die Einwilligung zum Schreiben erteilt ist - und das ist
    hier die einzige Stelle, an der das noch **vor** dem Google-Bildschirm
    passieren kann.

    Fehlt die Einwilligung, wird 422 mit Klartext geantwortet: Die Schreib-Scope
    wird nicht einmal angefragt, solange niemand sie erteilt hat.
    """
    source = _google_integration(db, integration_id, current_user.id)
    integration = _read(source, settings)
    if integration.missing_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing required values: {', '.join(integration.missing_fields)}",
        )
    if not calendar_consent.calendar_writing_allowed(db, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Für Google Kalender wird zuerst die Freigabe zum Schreiben gebraucht. "
                "Ohne sie wird nur gelesen, und die Verbindung hier startet gar nicht."
            ),
        )
    state = oauth_state.sign_state(source.id, current_user.id, _signing_secret(settings))
    try:
        url = google_calendar.build_authorization_url(
            client_id=integration.config.get("client_id", ""),
            redirect_uri=integration.config.get("redirect_uri", ""),
            state=state,
        )
    except google_calendar.GoogleOAuthError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    source.status = "pending"
    source.last_error = None
    db.commit()
    return IntegrationAuthorizeRead(
        authorization_url=url,
        state=state,
        scopes=list(google_calendar.SCOPES),
        message="Google zeigt jetzt an, welche Rechte die Verbindung braucht.",
    )


@router.get(
    "/{integration_id}/oauth/callback",
    response_class=HTMLResponse,
    responses={400: {"content": {"text/html": {}}}},
)
def oauth_callback(
    integration_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
) -> HTMLResponse:
    """Nimmt den Autorisierungscode entgegen und legt das Token verschlüsselt ab.

    Google schickt den Browser hierher. Weil der Aufruf über
    ``/api/backend/v1/...`` läuft, ist das dieselbe Herkunft wie die Oberfläche -
    das Sitzungs-Cookie ist da und ``current_user`` wird gefunden. Ohne Sitzung
    ist das 401, auch wenn Google gerade erfolgreich war; das ist der Preis
    dafür, dass keine fremde Seite den Code einschieben kann.

    Ein Fehler wird als **HTML mit Klartext** beantwortet und nicht als JSON mit
    Fehlercode: der Browser landet hier in einem Tab, nicht in einem
    ``fetch``. Ein 500 mit leerem Bildschirm wäre eine Sackgasse.
    """
    source = _google_integration(db, integration_id, current_user.id)
    key = _encryption_key(settings)

    if error.strip():
        return _authorization_page(
            "Die Verbindung wurde abgebrochen",
            f"Google meldet: {error.strip()}. Es wurden keine Daten gespeichert.",
        )

    try:
        claims = oauth_state.verify_state(state, _signing_secret(settings))
    except oauth_state.OAuthStateError as exc:
        return _authorization_page("Diese Verbindung passt nicht", str(exc))

    # Beide Bindungen prüfen: der state nennt Integration **und** Nutzer. Wäre
    # nur die Integration geprüft, könnte eine Lernende den state einer anderen
    # an eine eigene, noch leere Integration kleben.
    if claims.subject != source.id or claims.actor != current_user.id:
        return _authorization_page(
            "Diese Verbindung passt nicht",
            "Der state gehört zu einer anderen Autorisierung und wurde verworfen.",
        )

    credentials = decrypt_credentials(source.credentials, key) if source.credentials else {}
    integration = _read(source, settings)
    try:
        tokens = google_calendar.exchange_code(
            client_id=integration.config.get("client_id", ""),
            client_secret=credentials.get("client_secret", ""),
            code=code,
            redirect_uri=integration.config.get("redirect_uri", ""),
            timeout_seconds=settings.integration_timeout_seconds,
        )
    except (google_calendar.GoogleOAuthError, CalendarSyncError) as exc:
        source.status = "error"
        source.last_error = str(exc)
        db.commit()
        return _authorization_page("Google hat den Code nicht angenommen", str(exc))

    source.credentials = encrypt_credentials(google_calendar.tokens_to_credentials(tokens, credentials), key)
    source.status = "configured"
    source.last_error = None
    db.commit()
    logger.info("google_calendar_authorized integration_id=%s user_id=%s", source.id, current_user.id)
    return _authorization_page(
        "Google Kalender ist verbunden",
        "Die Termine werden ab jetzt mit deinem Kalender abgeglichen. "
        "Der Zugriff ist verschlüsselt gespeichert.",
    )


def _authorization_page(heading: str, message: str) -> HTMLResponse:
    """Eine schlichte deutsche Seite für das Ende des Autorisierungslaufs.

    Bewusst ohne Skript und ohne externes Bild: die Seite läuft unter der
    Content-Security-Policy der Oberfläche, und ein eingebettetes Skript wäre der
    einzige Grund, sie anpassen zu müssen. Der Text steht deshalb als
    HTML-Escape drin - Google liefert Fehlertexte zurück, und die sind Eingabe
    von außen.
    """
    body = (
        "<!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>{heading}</title></head>"
        "<body style=\"font-family: system-ui, sans-serif; margin: 3rem auto; max-width: 34rem; "
        "line-height: 1.6; color: #171717\">"
        "<h1 style=\"font-size: 1.25rem\">{heading}</h1>"
        "<p>{message}</p>"
        "<p><a href=\"/settings\">Zurück zu den Einstellungen</a></p>"
        "</body></html>"
    ).format(heading=html.escape(heading), message=html.escape(message))
    return HTMLResponse(content=body)
