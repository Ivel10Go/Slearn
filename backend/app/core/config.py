import json
from functools import lru_cache
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_PLACEHOLDER_SECRETS = frozenset(
    {
        "admin-password",
        "change-me",
        "change-me-local-only",
        "changeme",
        "development",
        "development-only-change-me",
        "development-only-not-a-secret",
        "example",
        "password",
        "placeholder",
        "replace-me",
        "schoolpass",
        "secret",
        "test-secret-key",
        "your-secret",
        "your-secret-key",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "School Assistant API"
    environment: str = Field(default="development", validation_alias=AliasChoices("APP_ENV", "environment"))
    debug: bool = Field(default=False, validation_alias=AliasChoices("DEBUG", "debug"))
    database_url: str = Field(
        default="postgresql+psycopg://schooluser:schoolpass@localhost:5432/school",
        validation_alias=AliasChoices("DATABASE_URL", "database_url"),
    )
    secret_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("SECRET_KEY", "secret_key"))
    integration_encryption_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("INTEGRATION_ENCRYPTION_KEY", "integration_encryption_key"),
    )
    integration_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
        validation_alias=AliasChoices("INTEGRATION_TIMEOUT_SECONDS", "integration_timeout_seconds"),
    )
    # Der planmäßige Abruf ist absichtlich nicht eingeschaltet. Wer ihn nicht
    # braucht, soll beim Start nicht ungefragt Netzverbindungen zu einem
    # Schulportal aufbauen; der Compose-Stack schaltet ihn ausdrücklich ein.
    # Ohne die zwei Werte bleibt der Weg über den Button in den Einstellungen.
    webuntis_sync_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("WEBUNTIS_SYNC_ENABLED", "webuntis_sync_enabled"),
    )
    webuntis_sync_interval_seconds: float = Field(
        default=24 * 60 * 60,
        gt=0,
        le=7 * 24 * 60 * 60,
        validation_alias=AliasChoices("WEBUNTIS_SYNC_INTERVAL_SECONDS", "webuntis_sync_interval_seconds"),
    )
    # Der Erinnerungslauf hat ein anderes Intervall als der WebUntis-Abruf und
    # eigene Fehlergrenzen. Auch er ist per Default aus: wer ihn nicht
    # einschaltet, soll beim Start keine Arbeit bekommen, die er nicht
    # bestellt hat. Der Knopf "Jetzt pruefen" in den Einstellungen geht
    # unabhaengig davon.
    reminder_scheduler_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("REMINDER_SCHEDULER_ENABLED", "reminder_scheduler_enabled"),
    )
    reminder_interval_seconds: float = Field(
        default=5 * 60,
        ge=60,
        le=24 * 60 * 60,
        validation_alias=AliasChoices("REMINDER_INTERVAL_SECONDS", "reminder_interval_seconds"),
    )
    # Web-Push. Der private Schluessel gehoert hier und **nicht** in den
    # Service Worker: der Service Worker laeuft im Browser und wuerde ihn an
    # jeden lesen, der die Datei bekommt. Fehlt der Schluessel, arbeitet der
    # ganze Erinnerungsweg ueber die geoeffnete Seite - es ist kein Fehler,
    # Push nicht eingerichtet zu haben.
    #
    # Jede `AliasChoices`-Liste fuehrt **drei** Schreibweisen: die
    # Umgebungsvariable, einen sprechenden Namen und den Feldnamen. Das dritte
    # entfaellt nicht - ohne es nimmt `Settings(web_push_private_key=...)` den
    # Wert nicht an, weil Pydantic bei gesetztem Alias den Feldnamen ignoriert.
    web_push_private_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "WEB_PUSH_VAPID_PRIVATE_KEY",
            "web_push_vapid_private_key",
            "web_push_private_key",
        ),
    )
    # Optional. Ohne diesen Wert wird der oeffentliche Schluessel aus dem
    # privaten berechnet; mit ihm muss er dazu passen, sonst koennte sich die
    # Seite fuer ein Schluesselpaar anmelden, mit dem der Server nicht
    # zeichnen kann.
    web_push_public_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "WEB_PUSH_VAPID_PUBLIC_KEY",
            "web_push_vapid_public_key",
            "web_push_public_key",
        ),
    )
    # Pflichtangabe des Push-Protokolls: eine Mailadresse oder eine HTTPS-
    # Adresse, unter der der Betreiber erreichbar ist. Ohne sie akzeptiert kein
    # echter Push-Dienst die Anfrage.
    web_push_subject: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "WEB_PUSH_VAPID_SUBJECT",
            "web_push_vapid_subject",
            "web_push_subject",
        ),
    )
    web_push_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
        validation_alias=AliasChoices(
            "WEB_PUSH_TIMEOUT_SECONDS",
            "web_push_timeout_seconds",
        ),
    )
    document_storage_path: str = Field(
        default="/var/lib/school-assistant/documents",
        validation_alias=AliasChoices("DOCUMENT_STORAGE_PATH", "document_storage_path"),
    )
    # Modelle bekommen einen **eigenen** Ablageort und nicht den Dokumentordner.
    # Drei Gruende: ein Modell ist gross und wird nicht durchsucht, der
    # Dokumentordner wird von einem Admin aufgeraeumt und waere dann ein
    # Loch in der Lernplan-Erstellung, und ein gemischter Ordner macht
    # `du -sh` unbrauchbar. Der Pfad ist der **interne** Container-Pfad; das
    # Host-Verzeichnis kommt als `MODELS_HOST_PATH` in docker-compose.yml dazu
    # und ist von hier aus getrennt - dasselbe Muster wie BACKUP_STORAGE_PATH
    # neben BACKUPS_HOST_PATH.
    model_storage_path: str = Field(
        default="/var/lib/school-assistant/models",
        validation_alias=AliasChoices("MODEL_STORAGE_PATH", "model_storage_path"),
    )
    # Lese-Timeout eines Blocks. Kein Gesamtlimit: ein 2-GB-Modell ueber eine
    # langsame Leitung darf sich Zeit nehmen, solange Daten kommen.
    model_download_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=600,
        validation_alias=AliasChoices("MODEL_DOWNLOAD_TIMEOUT_SECONDS", "model_download_timeout_seconds"),
    )
    # Reserve, die beim Download nie angefasst wird. Ohne sie fuellt ein
    # grosses Modell eine knappe Partition und reisst dabei PostgreSQL mit.
    model_min_free_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=0,
        validation_alias=AliasChoices("MODEL_MIN_FREE_BYTES", "model_min_free_bytes"),
    )
    # Reihenfolge der KI-Wege. **Standard ist lokal zuerst**, und das ist eine
    # Produktentscheidung, keine Voreinstellung aus Bequemlichkeit: ein lokaler
    # Server kostet nichts und versendet nichts. Wer es umkehrt, etwa weil der
    # lokale Weg im Schulnetz zu langsam ist, stellt es hier um - der Weg selbst
    # ist derselbe, es wird nur andersherum probiert.
    ai_local_first: bool = Field(
        default=True,
        validation_alias=AliasChoices("AI_LOCAL_FIRST", "ai_local_first"),
    )
    # Zeitbudget fuer einen lokalen Modelllauf, **wenn er der einzige Weg ist**.
    # Dann gibt es nichts, wofuer man sich beeilen koennte, und ein 8B-Modell auf
    # CPU darf sich Zeit nehmen. Bewusst weit ueber
    # `integration_timeout_seconds` - das ist der *Verbindungs*-test (10 s).
    ai_local_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=600,
        validation_alias=AliasChoices("AI_LOCAL_TIMEOUT_SECONDS", "ai_local_timeout_seconds"),
    )
    # Dasselbe Budget, aber **kuerzer**, wenn hinter dem lokalen Weg noch ein
    # Online-Provider wartet. Das ist das gestufte Zurueckfallen: ein lokales
    # Modell, das in 15 Sekunden nichts liefert, verstellt dem schnelleren Weg
    # nur die Reihenfolge. Ohne Provider im Rueckgrat gilt der grosse Wert.
    ai_local_fast_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        le=600,
        validation_alias=AliasChoices("AI_LOCAL_FAST_TIMEOUT_SECONDS", "ai_local_fast_timeout_seconds"),
    )
    # Zeitbudget fuer eine Generierung beim Online-Provider. Ebenso getrennt vom
    # Verbindungstest - ein Modelllauf dauert laenger als ein `GET /models`.
    ai_generate_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=600,
        validation_alias=AliasChoices("AI_GENERATE_TIMEOUT_SECONDS", "ai_generate_timeout_seconds"),
    )
    # Der Backup-Stand liegt in der Statusdatei des Workers. Das Backend greift
    # nicht auf den Worker zu, sondern liest dieselbe Datei - und das nur, wenn
    # beide das Volume `worker-status` teilen. Der Standardwert ist deshalb
    # bewusst unveraendert, auch wenn der Pfad in einem Aufruf nichts findet.
    worker_status_path: str = Field(
        default="/var/lib/school-assistant/worker-status/worker-status.json",
        validation_alias=AliasChoices("WORKER_STATUS_PATH", "worker_status_path"),
    )
    # --- Sprach-Assistent -------------------------------------------------
    # Alles hier ist **optional** und per Default aus. Der Grund ist derselbe
    # wie beim WebUntis-Abruf: ein nacktes `python -m uvicorn` soll beim Start
    # keine ungefragten Verbindungen aufbauen, und die Sprachfunktionen sollen
    # ohne Modelle vollstaendigstaendig lauffaehig sein.
    speech_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("SPEECH_ENABLED", "speech_enabled"),
    )
    # Die Laufzeiten liegen **ausserhalb** des Python-Prozesses: whisper.cpp
    # bringt seinen eigenen HTTP-Dienst mit, Piper ist ein Kommandozeilenwerk.
    # Beide Pfade sind deshalb nur ein Name im `PATH` - und `None` heisst
    # "eingetragen, aber nicht installiert", was die Faehigkeitsabfrage als
    # deutschen Satz ausgibt statt als Fehler.
    whisper_server_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("WHISPER_SERVER_URL", "whisper_server_url"),
    )
    whisper_server_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        le=600,
        validation_alias=AliasChoices("WHISPER_SERVER_TIMEOUT_SECONDS", "whisper_server_timeout_seconds"),
    )
    # Leer lassen heisst: die Sprache aus der Aufnahme selbst schaetzen lassen
    # und keine Sprachangabe mitschicken. Das ist fuer einen deutschen Lernraum
    # fast immer richtig und haelt den Aufruf portabel.
    whisper_language: str | None = Field(
        default=None,
        validation_alias=AliasChoices("WHISPER_LANGUAGE", "whisper_language"),
    )
    piper_binary_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PIPER_BINARY_PATH", "piper_binary_path"),
    )
    # Eingebaute Laufzeiten (app/services/local_runtimes.py): llama-server,
    # whisper-server und piper werden bei Bedarf von GitHub geladen und als
    # Kindprozesse gestartet. Die Desktop-App schaltet das ein; im Docker-Stack
    # bringt der Betreiber seine Laufzeiten selbst mit, deshalb Default aus.
    local_runtimes_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("LOCAL_RUNTIMES_ENABLED", "local_runtimes_enabled"),
    )
    # Hintergrund-Indexer fuer Fotos/Scans und Einbettungen (app/services/materials.py).
    # Auf dem Desktop laeuft er immer; auf einem Server nur, wenn eingeschaltet -
    # ohne eingebautes Bildmodell schreibt er ueber den Online-KI-Dienst ab.
    material_indexer_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MATERIAL_INDEXER_ENABLED", "material_indexer_enabled"),
    )
    # LoRA-Training (app/services/training_jobs.py): der Trainer ist ein
    # eigener Python mit torch/CUDA (trainer/requirements.txt), nicht das
    # Backend-Python. Leer = Training nicht eingerichtet.
    trainer_python: str = Field(
        default="",
        validation_alias=AliasChoices("TRAINER_PYTHON", "trainer_python"),
    )
    # Ordner mit lernraum_trainer.py; leer = trainer/ im Repository.
    trainer_dir: str = Field(
        default="",
        validation_alias=AliasChoices("TRAINER_DIR", "trainer_dir"),
    )
    # Wohin die App die Trainer-Umgebung einrichtet (app/services/trainer_install.py,
    # ~6 GB); leer = <MODEL_STORAGE_PATH>/trainer-env. Ein TRAINER_PYTHON schlaegt sie.
    trainer_env_path: str = Field(
        default="",
        validation_alias=AliasChoices("TRAINER_ENV_PATH", "trainer_env_path"),
    )
    # Hugging-Face-Ablage fuer die Basisgewichte (3B = ~6 GB); leer = unter MODEL_STORAGE_PATH.
    trainer_cache_path: str = Field(
        default="",
        validation_alias=AliasChoices("TRAINER_CACHE_PATH", "trainer_cache_path"),
    )
    # ffmpeg wird nur fuer alles gebraucht, was **nicht** bereits ein
    # 16-kHz-Mono-WAV ist - also praktisch immer, weil Chrome WebM und Safari
    # MP4 aufnehmen. Fehlt es, bleibt der Textmodus und eine WAV-Datei moeglich.
    ffmpeg_binary_path: str = Field(
        default="ffmpeg",
        validation_alias=AliasChoices("FFMPEG_BINARY_PATH", "ffmpeg_binary_path"),
    )
    # Obergrenze einer Aufnahme. Ohne sie ist ein `/transcribe`-Endpunkt eine
    # Einladung, den Arbeitsspeicher des Containers zu fuellen; 12 MiB sind
    # rund sieben Minuten Ton und damit mehr, als jemand diktiert.
    speech_max_upload_bytes: int = Field(
        default=12 * 1024 * 1024,
        ge=1024,
        le=64 * 1024 * 1024,
        validation_alias=AliasChoices("SPEECH_MAX_UPLOAD_BYTES", "speech_max_upload_bytes"),
    )
    speech_max_seconds: int = Field(
        default=120,
        ge=5,
        le=600,
        validation_alias=AliasChoices("SPEECH_MAX_SECONDS", "speech_max_seconds"),
    )
    speech_max_text_characters: int = Field(
        default=4000,
        ge=100,
        le=40000,
        validation_alias=AliasChoices("SPEECH_MAX_TEXT_CHARACTERS", "speech_max_text_characters"),
    )
    # Leeres Feld heisst: das kleinste geladene Modell nehmen, tiny vor base.
    # Ein Eintrag ist nur noetig, wenn jemand eine bestimmte Groesse erzwingen
    # will - und dann darf die Oberflaeche nicht still etwas anderes nehmen.
    speech_stt_model_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SPEECH_STT_MODEL_ID", "speech_stt_model_id"),
    )
    # Dasselbe fuer die Stimme. Leer heisst: eine **deutsche** geladene Stimme,
    # sonst die kleinste - aber nie stillschweigend eine andere Sprache.
    speech_tts_voice_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SPEECH_TTS_VOICE_ID", "speech_tts_voice_id"),
    )
    admin_username: str | None = Field(default=None, validation_alias=AliasChoices("ADMIN_USERNAME", "admin_username"))
    admin_password: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ADMIN_PASSWORD", "admin_password"),
    )
    bootstrap_on_startup: bool = Field(
        default=True,
        validation_alias=AliasChoices("BOOTSTRAP_ON_STARTUP", "bootstrap_on_startup"),
    )
    demo_mode: bool = Field(default=False, validation_alias=AliasChoices("DEMO_MODE", "demo_mode"))
    auth_enabled: bool = Field(default=True, validation_alias=AliasChoices("AUTH_ENABLED", "auth_enabled"))
    session_cookie_name: str = Field(
        default="school_session",
        validation_alias=AliasChoices("SESSION_COOKIE_NAME", "session_cookie_name"),
    )
    session_max_age_seconds: int = Field(
        default=60 * 60 * 24 * 7,
        gt=0,
        validation_alias=AliasChoices("SESSION_MAX_AGE_SECONDS", "session_max_age_seconds"),
    )
    cookie_secure: bool = Field(
        default=False,
        validation_alias=AliasChoices("SESSION_COOKIE_SECURE", "COOKIE_SECURE", "cookie_secure"),
    )
    cookie_httponly: bool = Field(
        default=True,
        validation_alias=AliasChoices("SESSION_COOKIE_HTTPONLY", "COOKIE_HTTPONLY", "cookie_httponly"),
    )
    cookie_samesite: Literal["lax", "strict", "none"] = Field(
        default="lax",
        validation_alias=AliasChoices("SESSION_COOKIE_SAMESITE", "COOKIE_SAMESITE", "cookie_samesite"),
    )
    cookie_domain: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SESSION_COOKIE_DOMAIN", "COOKIE_DOMAIN", "cookie_domain"),
    )
    login_rate_limit: int = Field(
        default=5,
        ge=1,
        validation_alias=AliasChoices("LOGIN_RATE_LIMIT", "login_rate_limit"),
    )
    login_rate_window_seconds: int = Field(
        default=60,
        ge=1,
        validation_alias=AliasChoices("LOGIN_RATE_WINDOW_SECONDS", "login_rate_window_seconds"),
    )
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias=AliasChoices("CORS_ORIGINS", "cors_origins"),
    )
    log_level: str = Field(default="INFO", validation_alias=AliasChoices("LOG_LEVEL", "log_level"))
    timezone: str = Field(
        default="UTC",
        validation_alias=AliasChoices("SCHOOL_TIMEZONE", "TIMEZONE", "timezone"),
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                parsed = json.loads(text)
                if not isinstance(parsed, list):
                    raise ValueError("CORS_ORIGINS must be a JSON array")
                return [str(origin).strip() for origin in parsed if str(origin).strip()]
            return [origin.strip() for origin in text.split(",") if origin.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(origin).strip() for origin in value if str(origin).strip()]
        raise ValueError("CORS_ORIGINS must be a comma-separated string or JSON array")

    @field_validator("cookie_samesite", mode="before")
    @classmethod
    def normalize_cookie_samesite(cls, value: object) -> str:
        normalized = str(value or "lax").strip().lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("cookie_samesite must be lax, strict, or none")
        return normalized

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}:
            raise ValueError("unsupported log level")
        return normalized

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Self:
        if self.environment.strip().lower() != "production":
            return self
        for field_name, label in (
            ("secret_key", "SECRET_KEY"),
            ("admin_password", "ADMIN_PASSWORD"),
            ("integration_encryption_key", "INTEGRATION_ENCRYPTION_KEY"),
        ):
            value = getattr(self, field_name)
            secret = value.get_secret_value() if isinstance(value, SecretStr) else (value or "")
            normalized = secret.strip()
            if not normalized or normalized.lower() in _PLACEHOLDER_SECRETS:
                raise ValueError(f"{label} must be a non-placeholder value in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def normalize_database_url(url: str) -> str:
    normalized = url.strip()
    if normalized.startswith("postgresql+psycopg2://"):
        return normalized.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    if normalized.startswith("postgres://"):
        return normalized.replace("postgres://", "postgresql+psycopg://", 1)
    if normalized.startswith("postgresql://"):
        return normalized.replace("postgresql://", "postgresql+psycopg://", 1)
    return normalized


def _secret_value(value: SecretStr | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value


def require_secret(settings: Settings) -> str:
    value = _secret_value(settings.secret_key).strip()
    if not value:
        raise RuntimeError("SECRET_KEY must be configured")
    return value


def require_admin_credentials(settings: Settings) -> tuple[str, str]:
    username = (settings.admin_username or "").strip()
    password = _secret_value(settings.admin_password)
    if not username or not password:
        raise RuntimeError("ADMIN_USERNAME and ADMIN_PASSWORD must both be configured")
    return username, password


def require_integration_encryption_key(settings: Settings) -> str:
    value = _secret_value(settings.integration_encryption_key).strip()
    if not value:
        raise RuntimeError("INTEGRATION_ENCRYPTION_KEY must be configured")
    return value


def require_web_push_private_key(settings: Settings) -> str:
    """Der private VAPID-Schluessel - oder ein klarer Grund, warum es ihn nicht gibt.

    Ein ``RuntimeError`` ist hier die *richtige* Sorte Fehler: es ist eine
    Konfigurationsfrage und kein Datenproblem, und genau so behandelt es der
    Endpunkt (503) wie der Scheduler.
    """
    value = _secret_value(settings.web_push_private_key).strip()
    if not value:
        raise RuntimeError("WEB_PUSH_VAPID_PRIVATE_KEY must be configured")
    return value


def require_web_push_subject(settings: Settings) -> str:
    """Die Kontaktangabe des Push-Protokolls (``mailto:`` oder ``https:``)."""
    value = (settings.web_push_subject or "").strip()
    if not value:
        raise RuntimeError("WEB_PUSH_VAPID_SUBJECT must be configured")
    if not value.startswith(("mailto:", "https://")):
        raise RuntimeError("WEB_PUSH_VAPID_SUBJECT must start with mailto: or https://")
    return value


def configured_web_push_public_key(settings: Settings) -> str | None:
    """Der oeffentliche Schluessel, **falls** einer eingetragen ist.

    Ein gesetzter Wert wird nicht gegengeprueft: das ist Aufgabe von
    ``app/services/web_push.py``, das Schluesselpaar als solches liest. Hier
    gibt es nur ``str`` zurueck oder ``None``.
    """
    return _secret_value(settings.web_push_public_key).strip() or None
