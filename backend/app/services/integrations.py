from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Final, cast

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, require_integration_encryption_key
from app.core.crypto import CredentialDecryptionError, decrypt_credentials, has_credentials
from app.db.entities import SyncSource
from app.schemas.integrations import IntegrationKind, IntegrationRead, IntegrationStatus
from app.services import calendar_consent, calendar_sync, google_calendar
from app.services.ai_provider import AiProviderError, probe_ai_provider
from app.services.caldav import DEFAULT_CALENDAR_PATH, CalDavClient
from app.services.calendar_sync import CalendarSyncError, CalendarSyncOutcome, IcalAdapter
from app.services.documents import DocumentSyncOutcome, sync_documents
from app.services.local_inference import RUNTIME_VALUES, describe_container_trap
from app.services.timetable_import import (
    DEFAULT_SYNC_WEEKS,
    SyncOutcome,
    import_timetable,
)
from app.services.webuntis import WebUntisClient, WebUntisError, probe_webuntis


class IntegrationValidationError(ValueError):
    pass


class IntegrationConnectionError(RuntimeError):
    """Die Integration ist vollständig konfiguriert, aber nicht erreichbar."""


@dataclass(frozen=True, slots=True)
class IntegrationSpec:
    kind: str
    label: str
    description: str
    required_config: tuple[str, ...] = ()
    #: Felder, die man **kann**, aber nicht muss. Sie werden mitgespeichert und
    #: mitgeschickt, aber nie als "fehlt" gemeldet.
    optional_config: tuple[str, ...] = ()
    required_credentials: tuple[str, ...] = ()
    optional_credentials: tuple[str, ...] = ()
    #: Gruppen, von denen **mindestens ein** Feld kommen muss. Für Quellen, die
    #: entweder eine Adresse *oder* eine Datei nehmen: eine flache Liste von
    #: Pflichtfeldern würde beide verlangen, und die Oberfläche zeigte einen
    #: Widerspruch, den es nicht gibt.
    required_one_of: tuple[tuple[str, ...], ...] = ()
    #: Kann die Quelle beschrieben werden? Nur hier steht ``True``, und nur
    #: dann fragt der Abgleich eine Schreib-Erlaubnis an - ein Abo wird nie
    #: beschrieben, egal welche Einwilligung vorliegt.
    supports_writing: bool = False

    @property
    def credential_keys(self) -> tuple[str, ...]:
        return self.required_credentials + self.optional_credentials

    @property
    def config_keys(self) -> tuple[str, ...]:
        allowed = set(self.required_config) | set(self.optional_config)
        for group in self.required_one_of:
            allowed.update(group)
        return tuple(sorted(allowed))


INTEGRATION_SPECS: Final[dict[str, IntegrationSpec]] = {
    "webuntis": IntegrationSpec(
        kind="webuntis",
        label="WebUntis",
        description="Importiert Stundenplan, Kurse und Klausuren aus deinem Schulportal.",
        required_config=("base_url", "school"),
        required_credentials=("username", "password"),
    ),
    "ai_provider": IntegrationSpec(
        kind="ai_provider",
        label="KI-Server",
        description=(
            "Ein OpenAI-kompatibler Server: lokal auf diesem Rechner (Ollama, LM Studio, llama.cpp) "
            "oder online (OpenAI und Ähnliches). Beide stehen absichtlich in derselben Art - "
            "eine lokale und eine Online-Verbindung brauchen dieselben Felder und dieselbe Prüfung."
        ),
        required_config=("base_url", "model"),
        # `runtime` nennt die Art des Servers und ist **optional**: ein Eintrag
        # von heute hat es nicht, und die Art wird dann aus der Adresse abgeleitet
        # (`app/services/local_inference.route_kind`). Ohne dieses Feld gaebe es
        # fuer lokal und online zwei Arten - und damit zwei Eintraege fuer eine
        # Verbindung, die sich sonst in keiner Weise unterscheidet.
        optional_config=("runtime",),
        optional_credentials=("api_key",),
    ),
    "documents": IntegrationSpec(
        kind="documents",
        label="Dokumente und Sync-Ordner",
        description="Lokaler Ablageort für Dokumente, die später durchsuchbar werden.",
        required_config=("path",),
    ),
    "ical": IntegrationSpec(
        kind="ical",
        label="iCalendar (ICS)",
        description=(
            "Liest Termine aus einer abonnierten ICS-Adresse oder einer ICS-Datei. "
            "Wird nur gelesen: ein Abo gehört jemand anderem."
        ),
        required_one_of=(("url", "path"),),
    ),
    "google_calendar": IntegrationSpec(
        kind="google_calendar",
        label="Google Kalender",
        description=(
            "Liest und schreibt Termine über OAuth 2.0. Google muss dafür Zugriff "
            "erlauben, und das Schreiben zusätzlich freigegeben werden."
        ),
        required_config=("client_id", "calendar_id", "redirect_uri"),
        # `refresh_token` ist bewusst **nicht** Pflicht: die Integration muss
        # angelegt werden können, *bevor* der Autorisierungslauf steht. Es
        # steht trotzdem in der Liste der erlaubten Felder, weil der Rückweg von
        # Google es nach dem Tausch dort ablegt.
        required_credentials=("client_secret",),
        optional_credentials=("refresh_token", "token_scope"),
        supports_writing=True,
    ),
    "caldav": IntegrationSpec(
        kind="caldav",
        label="Apple Kalender (CalDAV)",
        description=(
            "Liest und schreibt Termine über CalDAV, etwa mit dem app-spezifischen "
            "Passwort von iCloud."
        ),
        required_config=("base_url",),
        required_credentials=("username", "password"),
        supports_writing=True,
    ),
}

INTEGRATION_KINDS: Final[tuple[str, ...]] = tuple(INTEGRATION_SPECS)


def integration_spec(kind: str) -> IntegrationSpec:
    spec = INTEGRATION_SPECS.get(kind)
    if spec is None:
        raise IntegrationValidationError(f"Unsupported integration kind: {kind}")
    return spec


def _validate_base_url(value: str, field: str) -> str:
    cleaned = value.strip().rstrip("/")
    if not cleaned:
        raise IntegrationValidationError(f"{field} must not be empty")
    if not cleaned.startswith(("http://", "https://")):
        raise IntegrationValidationError(f"{field} must start with http:// or https://")
    if len(cleaned) > 500:
        raise IntegrationValidationError(f"{field} is too long")
    return cleaned


def _validate_documents_path(value: str, storage_root: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise IntegrationValidationError("path must not be empty")
    root = Path(storage_root)
    candidate = Path(cleaned)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise IntegrationValidationError("path must stay inside the configured document storage directory") from exc
    return str(resolved)


def normalize_config(kind: str, config: dict[str, str], storage_root: str) -> dict[str, str]:
    spec = integration_spec(kind)
    allowed = set(spec.config_keys)
    normalized: dict[str, str] = {}
    for key, raw_value in config.items():
        field = key.strip().lower()
        value = str(raw_value).strip()
        if field not in allowed:
            raise IntegrationValidationError(f"Unsupported field for {spec.label}: {key}")
        if not value:
            raise IntegrationValidationError(f"{field} must not be empty")
        if field in {"base_url", "url"}:
            value = _validate_base_url(value, field)
        elif field == "path":
            # Auch der ICS-Pfad wird gegen den Ablageordner geprüft. Er steht
            # dort genauso wie der Dokumentordner: eine Adresse, die auf
            # beliebige Dateien des Servers zeigt, wäre ein Loch.
            value = _validate_documents_path(value, storage_root)
        elif kind == "ai_provider" and field == "runtime":
            value = _validate_runtime(value)
        normalized[field] = value
    return normalized


def _validate_runtime(value: str) -> str:
    """Prueft das optionale Feld ``runtime``.

    Ein unbekannter Wert wird **abgelehnt** und nicht stillschweigend auf ``auto``
    gesetzt: wer aus einem Tippfehler in der Oberflaeche eine falsche Herkunft
    macht, sieht die Meldung sofort - ein still korrigiertes Feld waere eine
    Herkunftsangabe, die niemand nachgeprueft hat. Ein von Hand in die Datenbank
    geschriebener Wert faellt dagegen ueber ``ai_routing.normalize_runtime`` still
    auf ``auto`` zurueck, weil dort kein Formular mehr im Weg steht.
    """
    cleaned = value.strip().lower().replace("-", "_")
    if cleaned not in RUNTIME_VALUES:
        erlaubt = ", ".join(RUNTIME_VALUES)
        raise IntegrationValidationError(f"runtime must be one of: {erlaubt}. Die Angabe darf auch leer bleiben.")
    return cleaned


def normalize_credentials(kind: str, credentials: dict[str, str]) -> dict[str, str]:
    spec = integration_spec(kind)
    allowed = set(spec.credential_keys)
    normalized: dict[str, str] = {}
    for key, raw_value in credentials.items():
        field = key.strip().lower()
        if field not in allowed:
            raise IntegrationValidationError(f"Unsupported credential for {spec.label}: {key}")
        value = str(raw_value)
        if not value:
            raise IntegrationValidationError(f"{field} must not be empty")
        normalized[field] = value
    return normalized


def missing_fields(kind: str, config: dict[str, str] | None, credential_keys: list[str] | None) -> list[str]:
    """Was für diese Art von Integration noch fehlt - in lesbarer Sprache.

    Eine Gruppe aus ``required_one_of`` erscheint als **ein** Eintrag
    (``"url oder path"``), nicht als zwei. Zwei bedeutungslose Felder, von denen
    eines reicht, wären in der Oberfläche eine Frage, die sie nicht beantworten
    kann.
    """
    spec = integration_spec(kind)
    stored = config or {}
    present_credentials = set(credential_keys or [])
    missing = [field for field in spec.required_config if not stored.get(field)]
    for group in spec.required_one_of:
        if not any(stored.get(field) for field in group):
            missing.append(" oder ".join(group))
    missing.extend(field for field in spec.required_credentials if field not in present_credentials)
    return missing


def integration_read(source: SyncSource, encryption_key: str) -> IntegrationRead:
    integration_spec(source.source_type)
    config = {str(key): str(value) for key, value in (source.configuration or {}).items()}
    stored = has_credentials(source.credentials)
    credential_keys: list[str] = []
    if stored and source.credentials is not None:
        credential_keys = sorted(decrypt_credentials(source.credentials, encryption_key))
    status = "disabled" if not source.enabled else (source.status or "configured")
    return IntegrationRead(
        id=source.id,
        kind=cast(IntegrationKind, source.source_type),
        name=source.name,
        enabled=source.enabled,
        status=cast(IntegrationStatus, status),
        config=config,
        has_credentials=stored,
        credential_keys=credential_keys,
        missing_fields=missing_fields(source.source_type, config, credential_keys),
        last_error=source.last_error,
        last_tested_at=source.last_tested_at,
        last_sync_at=source.last_sync_at,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def check_integration_connection(
    kind: str,
    config: dict[str, str],
    credentials: dict[str, str],
    document_storage_path: str,
    timeout_seconds: float,
    timezone_name: str = "UTC",
) -> str:
    """Prüft eine Integration gegen das echte Gegenstück und gibt einen deutschen Text zurück.

    Wirft ``IntegrationConnectionError``, wenn die Verbindung nicht zustande kommt.

    ``timezone_name`` wird nur von den Kalenderquellen gebraucht: eine ICS-Datei
    darf Zeiten ohne Zone schreiben, und dann ist die Zone des Nutzerkontos die
    einzige getroffene Annahme (siehe ``app/services/ical.py``). Ohne diesen
    Parameter wäre der Verbindungstest eines Abos zwei Stunden daneben und würde
    trotzdem erfolgreich melden.
    """
    if kind == "webuntis":
        try:
            identity = probe_webuntis(
                base_url=config.get("base_url", ""),
                school=config.get("school", ""),
                username=credentials.get("username", ""),
                password=credentials.get("password", ""),
                timeout_seconds=timeout_seconds,
            )
        except WebUntisError as exc:
            raise IntegrationConnectionError(str(exc)) from exc
        return f"WebUntis ist erreichbar und die Zugangsdaten stimmen ({identity.describe()})."

    if kind == "ai_provider":
        base_url = config.get("base_url", "")
        try:
            models = probe_ai_provider(
                base_url=base_url,
                model=config.get("model", ""),
                api_key=credentials.get("api_key", ""),
                timeout_seconds=timeout_seconds,
            )
        except AiProviderError as exc:
            # Der haeufigste Grund fuer einen nicht erreichbaren **lokalen**
            # Server ist nicht ein falscher Port, sondern `localhost`: aus dem
            # Docker-Container zeigt das auf den Container selbst. Der Hinweis
            # steht deshalb in der Meldung, die die Oberflaeche anzeigt - nicht
            # in einem Kommentar, den niemand liest, wenn der Knopf rot wird.
            trap = describe_container_trap(base_url)
            raise IntegrationConnectionError(f"{exc} {trap}" if trap else str(exc)) from exc
        return f"Der KI-Server ist erreichbar und {models.describe()}"


    if kind == "documents":
        return _check_documents_path(config.get("path", ""), document_storage_path)

    if kind == "ical":
        try:
            return ical_adapter(config, document_storage_path, timezone_name, timeout_seconds).check_connection()
        except CalendarSyncError as exc:
            raise IntegrationConnectionError(str(exc)) from exc

    if kind == "google_calendar":
        if not credentials.get("refresh_token"):
            return google_calendar.describe_token_credentials(credentials)
        try:
            with google_calendar_client(config, credentials, timeout_seconds) as client:
                return client.check_connection()
        except CalendarSyncError as exc:
            raise IntegrationConnectionError(str(exc)) from exc

    if kind == "caldav":
        try:
            with caldav_client(config, credentials, timezone_name, timeout_seconds) as client:
                return client.check_connection()
        except CalendarSyncError as exc:
            raise IntegrationConnectionError(str(exc)) from exc

    raise IntegrationConnectionError(f"Für {kind} ist noch kein Adapter implementiert.")


def ical_adapter(
    config: dict[str, str],
    document_storage_path: str,
    timezone_name: str,
    timeout_seconds: float,
) -> IcalAdapter:
    """Baut den Lese-Adapter für ein iCalendar-Abo oder eine ICS-Datei."""
    return IcalAdapter(
        url=config.get("url", ""),
        path=config.get("path", ""),
        storage_root=document_storage_path,
        timezone_name=timezone_name,
        timeout_seconds=timeout_seconds,
    )


def google_calendar_client(
    config: dict[str, str],
    credentials: dict[str, str],
    timeout_seconds: float,
) -> google_calendar.GoogleCalendarClient:
    """Baut den Google-Adapter aus Konfiguration und Credential-Blase."""
    return google_calendar.GoogleCalendarClient(
        client_id=config.get("client_id", ""),
        client_secret=credentials.get("client_secret", ""),
        calendar_id=config.get("calendar_id", ""),
        tokens=google_calendar.OAuthTokens(
            refresh_token=credentials.get("refresh_token", ""),
            scope=credentials.get("token_scope", ""),
        ),
        timeout_seconds=timeout_seconds,
    )


def caldav_client(
    config: dict[str, str],
    credentials: dict[str, str],
    timezone_name: str,
    timeout_seconds: float,
) -> CalDavClient:
    """Baut den CalDAV-Adapter aus Konfiguration und Credential-Blase."""
    return CalDavClient(
        base_url=config.get("base_url", ""),
        username=credentials.get("username", ""),
        password=credentials.get("password", ""),
        calendar_path=config.get("calendar_path", "") or DEFAULT_CALENDAR_PATH,
        timezone_name=timezone_name,
        timeout_seconds=timeout_seconds,
    )


def _check_documents_path(path: str, storage_root: str) -> str:
    root = Path(storage_root).resolve()
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise IntegrationConnectionError("Der Dokumentpfad liegt außerhalb des konfigurierten Ablageordners.")
    if not resolved.exists():
        if not root.is_dir():
            raise IntegrationConnectionError(f"Der Ablageordner {root} existiert nicht.")
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise IntegrationConnectionError(f"Der Dokumentpfad ließ sich nicht anlegen: {exc.strerror}.") from exc
        return f"Der Dokumentordner {resolved.name or resolved} wurde angelegt und ist beschreibbar."
    if not resolved.is_dir():
        raise IntegrationConnectionError(f"{resolved} ist kein Ordner.")
    return f"Der Dokumentordner {resolved.name or resolved} ist vorhanden und beschreibbar."


def sync_window(today: date | None = None, weeks: int = DEFAULT_SYNC_WEEKS) -> tuple[date, date]:
    """Rechnet das Abruffenster aus: Montag der aktuellen Woche bis ``weeks`` Wochen.

    Der Start liegt bewusst am Montag, weil ein Schulwochenraster dort
    einheitlich beginnt; ein Sync am Mittwoch soll trotzdem dieselbe Woche
    liefern wie ein Sync am Montag.
    """
    reference = today or date.today()
    monday = reference - timedelta(days=reference.weekday())
    span = max(1, weeks)
    return monday, monday + timedelta(days=7 * span - 1)


CALENDAR_KINDS: Final[frozenset[str]] = frozenset({"ical", "google_calendar", "caldav"})


def calendar_adapter(
    kind: str,
    *,
    config: dict[str, str],
    credentials: dict[str, str],
    document_storage_path: str,
    timezone_name: str,
    timeout_seconds: float,
) -> calendar_sync.CalendarAdapter:
    """Der Adapter zu einer Kalenderquelle - eine Stelle, drei Protokolle."""
    if kind == "ical":
        return ical_adapter(config, document_storage_path, timezone_name, timeout_seconds)
    if kind == "google_calendar":
        return google_calendar_client(config, credentials, timeout_seconds)
    if kind == "caldav":
        return caldav_client(config, credentials, timezone_name, timeout_seconds)
    raise IntegrationValidationError(f"Für {kind} gibt es keinen Kalenderadapter.")


def sync_integration(
    *,
    db: Session,
    source: SyncSource,
    config: dict[str, str],
    credentials: dict[str, str],
    settings: Settings,
    timezone_name: str,
    timeout_seconds: float,
    weeks: int = DEFAULT_SYNC_WEEKS,
    today: date | None = None,
) -> SyncOutcome | DocumentSyncOutcome | CalendarSyncOutcome:
    """Holt Daten aus der Integration und schreibt sie in die Datenbank.

    Wirft ``IntegrationConnectionError``, wenn die Gegenseite nicht erreichbar
    ist oder ablehnt. Der Aufrufer entscheidet, was in ``last_error`` landet.
    """
    if source.source_type == "webuntis":
        return _sync_webuntis(
            db, source, config, credentials, timezone_name, timeout_seconds, weeks, today
        )
    if source.source_type == "documents":
        return sync_documents(
            db=db,
            source=source,
            folder=str(config.get("path", "")),
            settings=settings,
        )
    if source.source_type in CALENDAR_KINDS:
        return _sync_calendar(
            db=db,
            source=source,
            config=config,
            credentials=credentials,
            settings=settings,
            timezone_name=timezone_name,
            timeout_seconds=timeout_seconds,
            weeks=weeks,
            today=today,
        )
    raise IntegrationConnectionError(f"Für {source.source_type} ist noch kein Import implementiert.")


def _sync_calendar(
    *,
    db: Session,
    source: SyncSource,
    config: dict[str, str],
    credentials: dict[str, str],
    settings: Settings,
    timezone_name: str,
    timeout_seconds: float,
    weeks: int,
    today: date | None,
) -> CalendarSyncOutcome:
    """Ein Abgleich in beide Richtungen, für alle drei Kalenderquellen.

    Das Fenster ist das des WebUntis-Abrufs - Montag der Woche bis ``weeks``
    Wochen später. Der Grund ist nicht Bequemlichkeit: ein Termin, der im
    Fenster liegt, ist der einzige, über den wir überhaupt etwas wissen
    können. Alles andere bliebe ohnehin stehen.

    Das Schreiben passiert nur, wenn **beides** vorliegt: die Quelle kann es, und
    die Lernende hat es erlaubt. Beide Bedingungen stehen hier nebeneinander,
    damit keine der beiden versehentlich zur zweiten wird.
    """
    start, end = sync_window(today, weeks)
    adapter = calendar_adapter(
        source.source_type,
        config=config,
        credentials=credentials,
        document_storage_path=settings.document_storage_path,
        timezone_name=timezone_name,
        timeout_seconds=timeout_seconds,
    )
    allow_writing = adapter.supports_writing and calendar_consent.calendar_writing_allowed(db, source.user_id)
    try:
        return calendar_sync.sync_calendar(
            db,
            source,
            adapter,
            window_start=start,
            window_end=end,
            timezone_name=timezone_name,
            allow_writing=allow_writing,
        )
    except calendar_sync.CalendarSyncError as exc:
        # Umgesetzt auf den Fehlertyp, den `run_integration_sync` und der
        # Scheduler bereits kennen. Sonst käme eine nicht erreichbare
        # Kalenderquelle als 500 heraus statt als `last_error` mit deutschem
        # Satz - und ein 500 sieht nach einem Serverfehler aus, nicht nach einer
        # kaputten Fremdverbindung.
        raise IntegrationConnectionError(str(exc)) from exc


def _sync_webuntis(
    db: Session,
    source: SyncSource,
    config: dict[str, str],
    credentials: dict[str, str],
    timezone_name: str,
    timeout_seconds: float,
    weeks: int,
    today: date | None,
) -> SyncOutcome:
    start, end = sync_window(today, weeks)
    try:
        with WebUntisClient(
            base_url=config.get("base_url", ""),
            school=config.get("school", ""),
            username=credentials.get("username", ""),
            password=credentials.get("password", ""),
            timeout_seconds=timeout_seconds,
        ) as client:
            timetable = client.fetch_timetable(start, end)
    except WebUntisError as exc:
        raise IntegrationConnectionError(str(exc)) from exc
    return import_timetable(db, source, timetable, timezone_name)


@dataclass(frozen=True, slots=True)
class IntegrationSyncRun:
    """Ergebnis eines Abrufs, gleichgültig ob Button oder Scheduler ihn ausgelöst hat.

    Der planmäßige Lauf und der Knopf in den Einstellungen brauchen dieselben
    drei Ausgänge: einen deutschen Satz, den Zustand der Integrationszeile und
    gegebenenfalls die fehlenden Angaben. Deshalb steht der ganze Ablauf hier
    und nicht im Endpunkt.
    """

    ok: bool
    status: IntegrationStatus
    message: str
    missing_fields: tuple[str, ...] = ()
    outcome: SyncOutcome | DocumentSyncOutcome | CalendarSyncOutcome | None = None


def record_integration_sync_error(db: Session, source: SyncSource, message: str) -> IntegrationSyncRun:
    """Schreibt einen Fehler in ``last_error`` und lässt die Zeile sonst unangetastet.

    Zuerst wird zurückgerollt, weil ein fehlgeschlagener Import sonst eine halb
    geschriebene Sitzung zurückließe. Die Zeile wird danach neu gelesen, damit
    nicht der Zustand aus dem Identity-Map-Cache zurückgeschrieben wird.
    """
    source_id = source.id
    db.rollback()
    stored = db.get(SyncSource, source_id)
    if stored is None:
        return IntegrationSyncRun(ok=False, status="error", message=message)
    stored.status = "error"
    stored.last_error = message
    db.commit()
    return IntegrationSyncRun(ok=False, status="error", message=message)


def run_integration_sync(
    *,
    db: Session,
    source: SyncSource,
    config: dict[str, str],
    credential_keys: Sequence[str],
    settings: Settings,
    timezone_name: str,
    weeks: int = DEFAULT_SYNC_WEEKS,
    today: date | None = None,
) -> IntegrationSyncRun:
    """Holt die Daten einer Integration und hält deren Zustand aktuell.

    Die Reihenfolge ist bewusst dieselbe wie beim Verbindungstest: fehlende
    Angaben, deaktivierte Integration, dann erst der eigentliche Abruf. Jeder
    logisch fehlgeschlagene Versuch landet in ``last_error`` und wird als
    Ergebnis zurückgegeben, damit weder der Endpunkt noch der Scheduler eine
    Ausnahme nach außen geben muss. Nur ein fehlender
    ``INTEGRATION_ENCRYPTION_KEY`` wird als ``RuntimeError`` weitergereicht,
    weil das ein Konfigurations- und kein Datenproblem ist.

    Diese Funktion prüft ``last_sync_at`` bewusst **nicht**. Der planmäßige
    Lauf entscheidet vorab, ob eine Integration fällig ist
    (``scheduled_sync_is_due``), der manuelle Knopf nicht - sonst könnte ein
    Klick am selben Tag nichts mehr auslösen.
    """
    label = INTEGRATION_SPECS[source.source_type].label if source.source_type in INTEGRATION_SPECS else source.source_type

    gaps = missing_fields(source.source_type, config, list(credential_keys))
    if gaps:
        message = f"Fehlende Angaben: {', '.join(gaps)}"
        _store_sync_state(db, source, status="error", last_error=message)
        return IntegrationSyncRun(ok=False, status="error", message=message, missing_fields=tuple(gaps))

    if not source.enabled:
        message = f"{label} ist deaktiviert, es wurden keine Daten geholt."
        _store_sync_state(db, source, status="disabled", last_error=None)
        return IntegrationSyncRun(ok=False, status="disabled", message=message)

    key = require_integration_encryption_key(settings)
    credentials = decrypt_credentials(source.credentials, key) if source.credentials else {}
    try:
        outcome = sync_integration(
            db=db,
            source=source,
            config=config,
            credentials=credentials,
            settings=settings,
            timezone_name=timezone_name,
            timeout_seconds=settings.integration_timeout_seconds,
            weeks=weeks,
            today=today,
        )
    except (IntegrationConnectionError, CredentialDecryptionError) as exc:
        return record_integration_sync_error(db, source, str(exc))
    except SQLAlchemyError:
        db.rollback()
        raise

    return IntegrationSyncRun(
        ok=True,
        status="connected",
        message=outcome.describe(),
        outcome=outcome,
    )


def _store_sync_state(db: Session, source: SyncSource, *, status: IntegrationStatus, last_error: str | None) -> None:
    source.status = status
    source.last_error = last_error
    db.commit()
