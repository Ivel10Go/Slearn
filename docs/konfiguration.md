# Konfiguration

Alle Umgebungsvariablen des Compose-Stacks. `.env.example` ist die kommentierte Vorlage dazu.

`.env.example` ist die kommentierte, vollständige Compose-Vorlage. Leere Pflichtwerte sind beabsichtigt: Compose startet erst, wenn sie ersetzt wurden.

## Fail-closed Kernwerte

| Variable | Zweck |
| --- | --- |
| `POSTGRES_USER` | Erforderlicher PostgreSQL-Benutzer |
| `POSTGRES_PASSWORD` | Erforderliches PostgreSQL-Passwort |
| `POSTGRES_DB` | Erforderlicher PostgreSQL-Datenbankname |
| `DATABASE_URL` | Erforderliche SQLAlchemy-URL für Backend und Worker |
| `SECRET_KEY` | Erforderlicher Schlüssel für Session-Token |
| `INTEGRATION_ENCRYPTION_KEY` | Erforderlicher Schlüssel zum Verschlüsseln der Integrations-Zugangsdaten |
| `ADMIN_PASSWORD` | Erforderliches Passwort für einen neu anzulegenden Bootstrap-Admin |

## Backend

| Variable | Standardwert | Zweck |
| --- | --- | --- |
| `ADMIN_USERNAME` | `admin` | Benutzername des Bootstrap-Admins |
| `BOOTSTRAP_ON_STARTUP` | `true` | Bootstrap-Benutzer beim Start sicherstellen |
| `AUTH_ENABLED` | `true` | Serverseitige Authentifizierung aktivieren |
| `DEMO_MODE` | `false` | Explizite Demo-Daten in das Backend laden |
| `APP_ENV` | `development` | Anwendungsmodus |
| `DEBUG` | `false` | Backend-Debugmodus |
| `SESSION_COOKIE_NAME` | `school_session` | Name des Session-Cookies |
| `SESSION_MAX_AGE_SECONDS` | `604800` | Maximale Session-Lebensdauer |
| `SESSION_COOKIE_SECURE` | `false` | Secure-Flag; bei HTTPS auf `true`. Folgt nicht aus `APP_ENV`, siehe [Betrieb hinter HTTPS](server.md#betrieb-hinter-https) |
| `SESSION_COOKIE_HTTPONLY` | `true` | Cookie-Zugriff durch JavaScript verhindern |
| `SESSION_COOKIE_SAMESITE` | `lax` | `lax`, `strict` oder `none` |
| `SESSION_COOKIE_DOMAIN` | leer | Optionales Cookie-Domain |
| `LOGIN_RATE_LIMIT` | `5` | Fehlgeschlagene Log-ins pro Zeitfenster |
| `LOGIN_RATE_WINDOW_SECONDS` | `60` | Länge des Log-in-Fensters |
| `CORS_ORIGINS` | `http://localhost:3000` | Erlaubte Direktzugriffs-Origins |
| `LOG_LEVEL` | `INFO` | Backend-Logging |
| `SCHOOL_TIMEZONE` | `Europe/Berlin` | IANA-Zeitzone für Schul- und Kalenderdaten |

## Frontend

| Variable | Standardwert | Zweck |
| --- | --- | --- |
| `BACKEND_INTERNAL_URL` | `http://backend:8000` | Nur serverseitiges Proxy-Ziel des Next.js-Route-Handlers |
| `FRONTEND_PORT` | `3000` | Host-Port des Frontends; nur bei belegtem Port 3000 ändern |
| `NEXT_PUBLIC_ENABLE_DEMO_FALLBACK` | `false` | Im Browser sichtbarer Demo-Fallback |

## Worker und Storage

| Variable | Standardwert | Zweck |
| --- | --- | --- |
| `WORKER_POLL_INTERVAL_SECONDS` | `300` | Abstand der DB-Prüfungen |
| `WORKER_HEARTBEAT_INTERVAL_SECONDS` | `30` | Abstand der Status-Heartbeats |
| `WORKER_HEALTH_MAX_AGE_SECONDS` | `120` | Maximales Heartbeat-Alter |
| `WORKER_STATUS_PATH` | `/var/lib/school-assistant/worker-status/worker-status.json` | Statusdatei im eigenen Volume; liest auch das Backend, wenn es das Volume mit teilt |
| `DOCUMENTS_HOST_PATH` | `./data/documents` | Host-Quelle für Dokumente |
| `BACKUPS_HOST_PATH` | `./data/backups` | Host-Quelle für Backups |
| `IMPORTS_HOST_PATH` | `./data/imports` | Host-Quelle für Importe |
| `DOCUMENT_STORAGE_PATH` | `/var/lib/school-assistant/documents` | Stabiles internes Backend-/Worker-Ziel |
| `INTEGRATION_TIMEOUT_SECONDS` | `10` | Zeitlimit für den Verbindungstest einer Integration (1 bis 120) |
| `BACKUP_STORAGE_PATH` | `/var/lib/school-assistant/backups` | Stabiles internes Backend-/Worker-Ziel |
| `IMPORT_STORAGE_PATH` | `/var/lib/school-assistant/imports` | Stabiles internes Backend-/Worker-Ziel |

Backup-Runner (alle Werte wirken nur im Worker):

| Variable | Standardwert | Zweck |
| --- | --- | --- |
| `BACKUP_ENABLED` | `true` | Runner abschaltbar, ohne den Container anzuhalten |
| `BACKUP_SCHEDULE` | `0 2 * * *` | Fünfteiliger Cron-Ausdruck in `SCHOOL_TIMEZONE`, siehe [Backup und Restore](server.md#backup-und-restore). Unverständliche Werte schalten den Runner ab, statt sie zu raten |
| `BACKUP_RETENTION_DAYS` | `30` | Aufbewahrung in Tagen; `0` lässt genau das neueste Backup stehen |
| `BACKUP_TICK_SECONDS` | `60` | Wie oft der Runner nach einem fälligen Termin sieht |
| `BACKUP_CATCHUP_HOURS` | `72` | Wie weit zurück ein versäumter Termin nachgeholt wird |
| `BACKUP_TIMEOUT_SECONDS` | `1800` | Abbruchgrenze für einen laufenden `pg_dump` |
| `BACKUP_CONNECT_TIMEOUT_SECONDS` | `10` | Verbindungs-Timeout von `pg_dump` |
| `BACKUP_FILE_PREFIX` | `school` | Namenspräfix der Dump-Dateien; die Rotation erkennt nur dieses Muster |
| `BACKUP_PG_DUMP_BINARY` | leer | Pfad zum `pg_dump`, wenn es weder im PATH noch unter `/usr/lib/postgresql` liegt |

## Reservierte spätere Integrationen

Der KI-Provider wird pro Integration in der Oberfläche konfiguriert, nicht über diese Variablen. Die folgenden Werte sind daher noch ohne Wirkung.

| Variable | Zweck |
| --- | --- |
| `AI_BASE_URL` | Spätere KI-Provider-Basis-URL |
| `AI_API_KEY` | Späteres KI-Zugangstoken |
| `AI_MODEL` | Spätere Modellkennung |
| `AI_TIMEOUT_SECONDS` | Späteres Request-Timeout |
| `AI_MAX_TOKENS` | Späteres Antwortlimit |
| `ICAL_CALENDAR_URL` | Spätere iCalendar-Quelle |
| `CALENDAR_REFRESH_INTERVAL_SECONDS` | Späteres Aktualisierungsintervall |
