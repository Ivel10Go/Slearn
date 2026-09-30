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
    # Der Backup-Stand liegt in der Statusdatei des Workers. Das Backend greift
    # nicht auf den Worker zu, sondern liest dieselbe Datei - und das nur, wenn
    # beide das Volume `worker-status` teilen. Der Standardwert ist deshalb
    # bewusst unveraendert, auch wenn der Pfad in einem Aufruf nichts findet.
    worker_status_path: str = Field(
        default="/var/lib/school-assistant/worker-status/worker-status.json",
        validation_alias=AliasChoices("WORKER_STATUS_PATH", "worker_status_path"),
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
