# School Assistant

School Assistant ist eine lokal betreibbare Docker-Compose-Anwendung mit Next.js-Frontend, FastAPI-Backend, PostgreSQL 15 mit pgvector und einem schlanken Python-Worker. Der Backend-Stand umfasst serverseitige Session-Authentifizierung, ein Dashboard, Benutzerisolation und API-Routen für Fächer, Hausaufgaben, Klausuren, Kalendereinträge und Lernaufgaben.

WebUntis, KI-Provider und Dokumente sind als echte Integrationen umgesetzt: Der Verbindungstest spricht das JSON-RPC-Portal der Schule bzw. die OpenAI-kompatible Modellliste des KI-Anbieters an und meldet `connected`, `error` oder `disabled`. iCalendar-Abgleich, Dokumentauswertung und automatische Backups sind reserviert, aber noch nicht als ausführbare Integrationen enthalten.

## Architektur

```text
Browser
  |
  | same-origin /api/backend/*
  v
Next.js 16 Frontend
  |
  | serverseitiger Route-Handler-Proxy auf http://backend:8000
  v
FastAPI Backend
  |
  | DATABASE_URL
  v
PostgreSQL 15 + pgvector
  ^
  |
  | schlanker DB-Health-/Heartbeat-Worker
```

- `frontend` läuft als Next.js-Standalone-Image mit Node.js 22.
- Browseranfragen verwenden den same-origin-Pfad `/api/backend/*`.
- Der Next.js-Route-Handler unter `app/api/backend/[...path]/route.ts` leitet diese Anfragen serverseitig an `BACKEND_INTERNAL_URL` weiter. Im Compose-Stack ist das Ziel fest `http://backend:8000`; der Browser erhält diese interne URL nicht.
- `backend` startet ausschließlich `app.main:app` auf Port 8000.
- `worker` enthält nur Worker-Code und die Abhängigkeit `psycopg[binary]`. Er führt ausschließlich Datenbank-Healthchecks und Status-Heartbeats aus.
- `postgres` verwendet das Multi-Arch-Image `pgvector/pgvector:0.8.1-pg15-bookworm` mit Linux-AMD64- und Linux-ARM64-Manifest.
- Alle Compose-Portbindungen sind auf `127.0.0.1` begrenzt.

## Voraussetzungen

- Docker Engine mit Compose-v2-Plugin.
- Freie Host-Ports `${FRONTEND_PORT:-3000}`, `5432` und `8000`.
- Schreibrechte für die drei konfigurierten Storage-Verzeichnisse.
- Für einen nativen Frontend-Start Node.js 22 oder neuer und npm.
- Für native Python-Tests Python 3.12 oder neuer.

Für den normalen Compose-Betrieb werden Node.js und Python nicht auf dem Host benötigt.

## Erste Einrichtung

Linux und macOS:

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Vor dem ersten Start müssen diese fail-closed Variablen mit nichtleeren, individuellen Werten ausgefüllt werden:

- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`
- `DATABASE_URL`
- `SECRET_KEY`
- `INTEGRATION_ENCRYPTION_KEY`
- `ADMIN_PASSWORD`

Die Vorlage enthält bei Passwörtern und `SECRET_KEY` absichtlich keine funktionierenden Platzhalter. `SECRET_KEY` muss ein ausreichend langer zufälliger Wert sein. `INTEGRATION_ENCRYPTION_KEY` schützt die gespeicherten Zugangsdaten der Integrationen (WebUntis, KI-Provider) und wird per HKDF zu einem AES-GCM-Schlüssel abgeleitet. Ein späterer Wechsel dieses Werts macht bereits gespeicherte Zugangsdaten unlesbar, also vor der ersten Nutzung festlegen. Datenbankpasswörter in `DATABASE_URL` müssen URL-kodiert werden. Wenn Benutzername oder Datenbankname geändert werden, müssen die Werte in `DATABASE_URL` dazu passen.

Der Bootstrap-Administrator wird nur angelegt, wenn er noch nicht existiert. Ein späteres Ändern von `ADMIN_PASSWORD` in `.env` rotiert das Passwort eines bereits vorhandenen Benutzers nicht automatisch.

Konfiguration prüfen und Stack starten:

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Nach erfolgreichem Start:

- Frontend: `http://localhost:${FRONTEND_PORT:-3000}`
- Backend: `http://localhost:8000`
- OpenAPI: `http://localhost:8000/docs`
- PostgreSQL auf dem Host: `127.0.0.1:5432`

Belegt ein anderer lokaler Dienst bereits Port 3000, in `.env` `FRONTEND_PORT` auf einen freien Port setzen, zum Beispiel `3002`. Die interne Container-Portbindung des Frontends bleibt 3000.

## Frontend-Proxy

Der Browser ruft ausschließlich den same-origin-Pfad `/api/backend/...` auf. Der Next.js-Server verwendet den Route-Handler `app/api/backend/[...path]/route.ts`:

```text
/api/backend/:path* -> http://backend:8000/api/:path*
```

`BACKEND_INTERNAL_URL` wird nur an den Next.js-Service übergeben. Es darf kein `NEXT_PUBLIC_`-Präfix erhalten und wird nicht an Backend oder Worker weitergereicht.

Für einen nativen `npm run dev`-Prozess muss der Next.js-Server stattdessen auf das Host-Backend zeigen. PowerShell:

```powershell
$env:BACKEND_INTERNAL_URL = "http://localhost:8000"
npm run dev
```

Linux und macOS:

```bash
BACKEND_INTERNAL_URL=http://localhost:8000 npm run dev
```

## API-Vertrag

Die nichtöffentliche Backend-Basis lautet `http://backend:8000`. Über das Frontend entspricht `/api/backend/<pfad>` dem Backend-Pfad `/api/<pfad>`.

| Bereich | Methode und Pfad | Zweck |
| --- | --- | --- |
| Health | `GET /health` | Gesamtstatus einschließlich Datenbank; bei Fehlern HTTP 503 |
| Liveness | `GET /health/live` | Prozess-Liveness ohne Datenbankabfrage |
| Auth | `POST /api/v1/auth/login` | Anmeldung und Setzen des Session-Cookies |
| Auth | `POST /api/v1/auth/logout` | Session-Cookie löschen |
| Auth | `GET /api/v1/auth/me` | Aktuellen Benutzer lesen |
| Dashboard | `GET /api/v1/dashboard` | Aggregierte Lern-, Hausaufgaben-, Klausur- und Kalenderdaten |
| Stundenplan | `GET /api/v1/lessons` | Stunden für einen Zeitraum; optionale `start`/`end` als `YYYY-MM-DD`, beide inklusiv, in der Zeitzone des Benutzers, maximal 62 Tage. Liefert zusätzlich `subject_short_name` (Fachkürzel aus dem Schulportal) und `is_cancelled` (ausgefallen) |
| Willkommenssatz | `GET /api/v1/greeting` | Tagesbriefing aus Stundenplan, Kalender und offenen Aufgaben. Wird vom KI-Provider erzeugt und pro Tag gehalten; ohne Provider kommt ein Satz aus den Daten (`source: "fallback"`) |
| Willkommenssatz | `POST /api/v1/greeting/refresh` | Satz neu formulieren; kostet einen Modellaufruf |
| Fächer | `GET, POST /api/v1/subjects` | Liste und Anlage |
| Fach | `GET, PATCH, DELETE /api/v1/subjects/{subject_id}` | Einzelnes Fach lesen, ändern oder löschen |
| Hausaufgaben | `GET, POST /api/v1/homework` | Liste mit `completed`-Filter und Anlage |
| Hausaufgabe | `GET, PATCH, DELETE /api/v1/homework/{homework_id}` | Einzelne Hausaufgabe verwalten |
| Hausaufgabe | `POST /api/v1/homework/{homework_id}/complete` | Als erledigt markieren |
| Hausaufgabe | `POST /api/v1/homework/{homework_id}/toggle` | Erledigtstatus umschalten, wenn kein Body gesendet wird |
| Hausaufgabe | `PATCH /api/v1/homework/{homework_id}/completed` | Expliziten Erledigtstatus setzen |
| Klausuren | `GET, POST /api/v1/exams` | Liste mit `upcoming`-Filter und Anlage |
| Klausur | `GET, PATCH, DELETE /api/v1/exams/{exam_id}` | Einzelne Klausur verwalten |
| Kalender | `GET, POST /api/v1/calendar` | Bevorstehende Ereignisse listen oder anlegen |
| Kalender | `GET, POST /api/v1/calendar/events` | Alias der Kalenderrouten |
| Kalenderereignis | `GET, PATCH, DELETE /api/v1/calendar/events/{event_id}` | Einzelnes Ereignis verwalten |
| Lernaufgabe | `GET, POST /api/v1/study/tasks` | Aufgaben listen oder anlegen; Liste unterstützt `status` |
| Lernaufgabe | `GET, PATCH, DELETE /api/v1/study/tasks/{task_id}` | Einzelne Lernaufgabe verwalten |
| Lernsession | `POST /api/v1/study/tasks/{task_id}/start` | Lernsession starten; idempotent für denselben Benutzer und dieselbe Aufgabe |
| Lernsession | `POST /api/v1/study/tasks/{task_id}/stop` | Offene Lernsession beenden und gelernte Sekunden speichern |
| Lernsession | `POST /api/v1/study/tasks/{task_id}/complete` | Aufgabe als erledigt markieren und offene Session abschließen |
| Integrationen | `GET /api/v1/integrations` | Eigene Integrationen inklusive Status und `last_sync_at` |
| Integration | `POST /api/v1/integrations` | Anlegen aus dem Katalog; Zugangsdaten werden verschlüsselt gespeichert |
| Integration | `PATCH /api/v1/integrations/{integration_id}` | Name, Config, Zugangsdaten oder `enabled` ändern |
| Integration | `DELETE /api/v1/integrations/{integration_id}` | Integration samt importierten Daten entfernen |
| Integrationstest | `POST /api/v1/integrations/{integration_id}/test` | Echte Verbindung prüfen; `missing_fields` → `enabled` → Verbindungstest |
| Integrationstest | `GET /api/v1/integrations/{integration_id}/test` | Letztes Testergebnis lesen, ohne erneut zu testen |
| Integrationskatalog | `GET /api/v1/integrations/catalog` | Kinds, Pflichtfelder und Feldbeschreibungen; ohne Session und ohne Schlüssel lesbar |
| Datensync | `POST /api/v1/integrations/{integration_id}/sync` | Daten importieren; aktuell `webuntis`, siehe unten |


Alle `/api/v1`-Routen außer Health erfordern standardmäßig eine gültige Session und isolieren Daten nach Benutzer. Die interaktive OpenAPI-Oberfläche ist unter `/docs`, die ReDoc-Oberfläche unter `/redoc` verfügbar.

### Stundenplan-Import aus WebUntis

`POST /api/v1/integrations/{integration_id}/sync` holt den Stundenplan direkt beim Schulportal und schreibt ihn in die Datenbank. Die Einbindung in den Einstellungen unter dem Namen **Stundenplan aktualisieren**; das Ergebnis erscheint danach im Dashboard. Es gibt keinen Request-Body und keinen Zeitraum-Parameter, der Import umfasst immer vier Kalenderwochen.

Die Reihenfolge ist dieselbe wie beim Verbindungstest: fehlende Felder → deaktivierte Integration → Schlüssel `INTEGRATION_ENCRYPTION_KEY` → echter Abruf. Ein logisch fehlgeschlagener Import antwortet wie `/test` mit HTTP 200, `ok: false` und deutschem `message`; der Fehlertext landet zusätzlich in `last_error` der Integration. Nur HTTP-Statusfehler des Stapels selbst (fehlende Session, fremde Integration, fehlender Schlüssel) ergeben 4xx bzw. 503.

Antwort zusätzlich zu den Feldern des Verbindungstests: `imported_lessons`, `subjects`, `teachers`, `rooms`, `skipped_entries`, `window_start`, `window_end` und `last_sync_at`.

Intern nutzt der Adapter die JSON-RPC-Methode `getTimetable` des Schulportals, aufgerufen über `{base_url}/WebUntis/jsonrpc.do?school={kuerzel}`. Die Parameter werden als **Objekt** übergeben, nicht als positionelles Array:

```json
{
  "id": 0,
  "method": "getTimetable",
  "params": {
    "options": {
      "id": "<Unixzeit in Millisekunden>",
      "element": { "id": "<personId>", "type": 5 },
      "type": 5,
      "startDate": "20260921",
      "endDate": "20261018",
      "showCanceledLessons": false,
      "showLessonType": false,
      "showRegistration": false,
      "showExams": false,
      "showSubstitutes": false,
      "showAdditionalInfo": false,
      "showRoomInfo": true,
      "showTeacherInfo": true,
      "showStudentgroup": true,
      "showLsText": true,
      "showLsNumber": true,
      "showSubstText": true,
      "showInfo": true,
      "showBooking": true,
      "classFields": ["id", "name", "longname", "externalkey"],
      "roomFields": ["id", "name", "longname", "externalkey"],
      "subjectFields": ["id", "name", "longname", "externalkey"],
      "teacherFields": ["id", "name", "longname", "externalkey"]
    }
  }
}
```

`element.type` ist die Element-ID der angemeldeten Person und stammt aus `personId`/`personType` der Login-Antwort: Schüler `5`, Klasse `1`, Lehrer `2`. `startDate` und `endDate` sind `YYYYMMDD` ohne Trennzeichen. WebUntis-Zeiten sind Ortszeit des Schulstandorts; der Import interpretiert sie in der Zeitzone des Benutzers und speichert sie als UTC.

Die Antwort ist ein Objekt mit `id` auf School-Ebene, `klasse`/`teachers`/`rooms`/`subjects` als Arrays von Stammdaten und `timeGrid` als Array der Unterrichtseinheiten. Eine Einheit liefert `id`, `date` (`YYYYMMDD`), `startTime`/`endTime` (`HHMM`), die Relationsarrays `kl`/`te`/`su`/`ro` mit Objekten aus den Stammdaten, `lsnumber`, `activityType` sowie optional `lstext` und `code`. Anlagen ohne `id`, `date`, `startTime` oder `endTime` werden übersprungen und in `skipped_entries` gezählt, statt den ganzen Import abbrechen zu lassen.

Fächer und Lehrer übernehmen `longname`, Räume bevorzugt `name`, weil `longname` dort teils Klassen- oder Gebäudenamen enthält. Existiert eine Stammdateneintragung schon manuell, wird sie anhand des Namens wiederverwendet, damit Fächer nicht doppelt auftauchen. Der Import ersetzt ausschließlich Lessons derselben `sync_source_id` innerhalb des Vier-Wochen-Fensters; manuell angelegte Stunden und Daten außerhalb des Fensters bleiben erhalten. `code` und `lsnumber` werden bewusst nicht gespeichert, weil die Modelle keine Felder dafür vorsehen.

Automatisch ausgelöst wird der Import nicht: Es gibt noch keinen Scheduler-Job. Neu deployte Container starten ohne Import, die Daten bleiben also bestehen, bis der Button in den Einstellungen gedrückt wird.


## Worker

Der Worker ist absichtlich klein und besitzt keinen HTTP-Port, kein Backend-Image und keine Hintergrundjobs. Er verbindet sich direkt mit psycopg, führt `SELECT 1` aus und schreibt einen nicht geheimen Status-Heartbeat.

Der Docker-Healthcheck ist bewusst strikt: Nur die Kombination

```text
status == "running"
database == "healthy"
```

und ein nicht veralteter, zeitzonenbehafteter Heartbeat gilt als gesund. `degraded`, `starting`, `stopping`, `stopped`, `unchecked`, `unhealthy` und fehlende oder ungültige Heartbeats sind unhealthy.

Worker-Status:

```bash
docker compose logs -f worker
docker compose exec worker cat /var/lib/school-assistant/worker-status/worker-status.json
```

Die Datei liegt im eigenen Named Volume `worker-status`. Sie enthält Prozessstatus, PID, Heartbeat, letzte Datenbankprüfung und nur den Fehlertyp. Sie enthält weder Zugangsdaten noch Dokumentinhalte.

Sicherheits- und Mount-Regeln:

- Image und Compose laufen mit UID/GID `10001:10001`.
- Das Root-Dateisystem ist read-only; nur das eigene Statusverzeichnis ist beschreibbar.
- Alle Linux-Capabilities werden entfernt und `no-new-privileges` ist aktiv.
- Dokumente, Backups und Importe sind im Worker read-only eingebunden.
- Nur das Statusverzeichnis ist für den Worker beschreibbar.
- Der Build-Kontext enthält ausschließlich `worker/`.

## Storage und Datenbank

| Host-Pfad-Variable | Standardpfad | Backend | Worker |
| --- | --- | --- | --- |
| `DOCUMENTS_HOST_PATH` | `./data/documents` | read/write | read-only |
| `BACKUPS_HOST_PATH` | `./data/backups` | read/write | read-only |
| `IMPORTS_HOST_PATH` | `./data/imports` | read/write | read-only |

Die absoluten Containerziele sind fest:

- `/var/lib/school-assistant/documents`
- `/var/lib/school-assistant/backups`
- `/var/lib/school-assistant/imports`
- `/var/lib/school-assistant/worker-status`

Die drei Host-Pfade dürfen in `.env` optional auf andere absolute Verzeichnisse gesetzt werden. Compose legt fehlende relative oder absolute Bind-Ziele bei Bedarf an. Die Datenbank liegt dagegen im Named Volume `pgdata`, der Worker-Status im Named Volume `worker-status`.

`postgres/init/001-vector.sql` aktiviert pgvector bei einer neuen Datenbank. Die Alembic-Revisionen bauen das Anwendungsschema auf:

- `20260925_0001_initial_schema` erstellt das vollständige Basisschema einschließlich pgvector-Unterstützung.
- `20260925_0002_open_study_sessions` ergänzt die eindeutige Beschränkung für höchstens eine offene Lernsession je Benutzer und Aufgabe.

Der Backend-Entrypoint ist eine POSIX-Shell-Datei und wird ausdrücklich mit `/bin/sh` gestartet. Er führt zuerst aus:

```bash
alembic -c alembic.ini upgrade head
```

Nur bei erfolgreicher Migration startet er Uvicorn per `exec`. Manuelle Alembic-Abfragen im laufenden Backend:

```bash
docker compose exec backend alembic -c alembic.ini current
docker compose exec backend alembic -c alembic.ini history
```

Änderungen an `POSTGRES_USER`, `POSTGRES_PASSWORD` oder `POSTGRES_DB` wirken nicht automatisch auf ein bereits initialisiertes `pgdata`-Volume.

Es ist noch kein Backup-Runner enthalten. Die Backup- und Kalenderintegrationen sind daher reserviert; `BACKUP_SCHEDULE` und `BACKUP_RETENTION_DAYS` werden noch nicht ausgeführt.

## Konfiguration

`.env.example` ist die kommentierte, vollständige Compose-Vorlage. Leere Pflichtwerte sind beabsichtigt: Compose startet erst, wenn sie ersetzt wurden.

### Fail-closed Kernwerte

| Variable | Zweck |
| --- | --- |
| `POSTGRES_USER` | Erforderlicher PostgreSQL-Benutzer |
| `POSTGRES_PASSWORD` | Erforderliches PostgreSQL-Passwort |
| `POSTGRES_DB` | Erforderlicher PostgreSQL-Datenbankname |
| `DATABASE_URL` | Erforderliche SQLAlchemy-URL für Backend und Worker |
| `SECRET_KEY` | Erforderlicher Schlüssel für Session-Token |
| `INTEGRATION_ENCRYPTION_KEY` | Erforderlicher Schlüssel zum Verschlüsseln der Integrations-Zugangsdaten |
| `ADMIN_PASSWORD` | Erforderliches Passwort für einen neu anzulegenden Bootstrap-Admin |

### Backend

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
| `SESSION_COOKIE_SECURE` | `false` | Secure-Flag; bei HTTPS auf `true` |
| `SESSION_COOKIE_HTTPONLY` | `true` | Cookie-Zugriff durch JavaScript verhindern |
| `SESSION_COOKIE_SAMESITE` | `lax` | `lax`, `strict` oder `none` |
| `SESSION_COOKIE_DOMAIN` | leer | Optionales Cookie-Domain |
| `LOGIN_RATE_LIMIT` | `5` | Fehlgeschlagene Log-ins pro Zeitfenster |
| `LOGIN_RATE_WINDOW_SECONDS` | `60` | Länge des Log-in-Fensters |
| `CORS_ORIGINS` | `http://localhost:3000` | Erlaubte Direktzugriffs-Origins |
| `LOG_LEVEL` | `INFO` | Backend-Logging |
| `SCHOOL_TIMEZONE` | `Europe/Berlin` | IANA-Zeitzone für Schul- und Kalenderdaten |

### Frontend

| Variable | Standardwert | Zweck |
| --- | --- | --- |
| `BACKEND_INTERNAL_URL` | `http://backend:8000` | Nur serverseitiges Proxy-Ziel des Next.js-Route-Handlers |
| `FRONTEND_PORT` | `3000` | Host-Port des Frontends; nur bei belegtem Port 3000 ändern |
| `NEXT_PUBLIC_ENABLE_DEMO_FALLBACK` | `false` | Im Browser sichtbarer Demo-Fallback |

### Worker und Storage

| Variable | Standardwert | Zweck |
| --- | --- | --- |
| `WORKER_POLL_INTERVAL_SECONDS` | `300` | Abstand der DB-Prüfungen |
| `WORKER_HEARTBEAT_INTERVAL_SECONDS` | `30` | Abstand der Status-Heartbeats |
| `WORKER_HEALTH_MAX_AGE_SECONDS` | `120` | Maximales Heartbeat-Alter |
| `WORKER_STATUS_PATH` | `/var/lib/school-assistant/worker-status/worker-status.json` | Statusdatei im eigenen Volume |
| `DOCUMENTS_HOST_PATH` | `./data/documents` | Host-Quelle für Dokumente |
| `BACKUPS_HOST_PATH` | `./data/backups` | Host-Quelle für Backups |
| `IMPORTS_HOST_PATH` | `./data/imports` | Host-Quelle für Importe |
| `DOCUMENT_STORAGE_PATH` | `/var/lib/school-assistant/documents` | Stabiles internes Backend-/Worker-Ziel |
| `INTEGRATION_TIMEOUT_SECONDS` | `10` | Zeitlimit für den Verbindungstest einer Integration (1 bis 120) |
| `BACKUP_STORAGE_PATH` | `/var/lib/school-assistant/backups` | Stabiles internes Backend-/Worker-Ziel |
| `IMPORT_STORAGE_PATH` | `/var/lib/school-assistant/imports` | Stabiles internes Backend-/Worker-Ziel |
| `BACKUP_SCHEDULE` | `0 2 * * *` | Reservierter Zeitplan ohne Runner |
| `BACKUP_RETENTION_DAYS` | `30` | Reservierte Aufbewahrung ohne Runner |

### Reservierte spätere Integrationen

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

## Tests, Lint, Typecheck und Builds

### Worker

Vom Repository-Root:

```bash
python -m unittest discover -s worker/tests -v
python -m py_compile worker/main.py worker/healthcheck.py
```

ARM64-Buildcheck des isolierten Worker-Images:

```bash
docker buildx build --platform linux/arm64 --file worker/Dockerfile --tag school-assistant-worker:arm64-check .
```

### Backend

```bash
cd backend
python -m pytest
python -m ruff check app alembic tests
python -m mypy app
```

Die benötigten Entwicklungsabhängigkeiten stehen in `backend/pyproject.toml` unter `dev`.

### Frontend

```bash
cd frontend
npm test
npm run lint
npm run typecheck
npm run build
```

Frontend und Backend benötigen für einen nativen Start ihre eigenen Abhängigkeiten. Der normale Compose-Build installiert und kapselt sie in den jeweiligen Images.

### Compose

```bash
docker compose config --quiet
```

Die vollständige Ausgabe von `docker compose config` enthält interpolierte Zugangsdaten und darf nicht geteilt oder ungeschützt protokolliert werden.

## Entwicklung mit Compose

```bash
docker compose up --build
docker compose ps
docker compose logs -f backend frontend worker
docker compose restart backend
docker compose up -d --build backend worker
docker compose down
```

`docker compose down` entfernt die Container, aber nicht `pgdata`, `worker-status` oder die Host-Verzeichnisse. `docker compose down -v` löscht beide Named Volumes und darf nur nach bestätigter Sicherung verwendet werden.

## Security

- Compose besitzt keine Default-Passwörter und keinen Default-Secret-Key für Kernwerte.
- `.env`, `.env.*` und andere lokale Konfigurationen werden durch `.gitignore` ausgeschlossen; `.env.example` bleibt als dokumentierte Vorlage sichtbar.
- Alle Host-Portbindungen verwenden `127.0.0.1`; PostgreSQL ist nicht öffentlich erreichbar.
- Das Frontend erhält keine Datenbank-, Session- oder Admin-Secrets.
- `BACKEND_INTERNAL_URL` ist serverintern und darf nicht mit `NEXT_PUBLIC_` Präfix verwendet werden.
- `SESSION_COOKIE_SECURE=true` ist für eine Veröffentlichung über HTTPS erforderlich.
- `CORS_ORIGINS` sollte nur tatsächlich benötigte Origin(s) enthalten.
- Der Worker läuft ohne Root-Rechte, ohne Linux-Capabilities, mit read-only Root-Dateisystem und read-only Datenzugriffen.
- Automatische Backups, Restore-Tests und Secret-Rotation sind noch nicht eingerichtet.
- Eine spätere Veröffentlichung sollte TLS, Reverse-Proxy, Authentifizierung, Cookies, CORS, Rate-Limits, Backups und Log-Schutz gemeinsam prüfen.

## Troubleshooting

### Compose verlangt eine Pflichtvariable

```bash
docker compose config --quiet
```

Die Fehlermeldung nennt die fehlende oder leere Variable. Leere Werte in `.env.example` sind absichtlich und müssen ersetzt werden.

### Backend bleibt unhealthy

```bash
docker compose logs backend
docker compose ps
```

Der häufigste Grund ist eine fehlgeschlagene Alembic-Migration oder eine `DATABASE_URL`, die nicht zu den PostgreSQL-Initialwerten passt. Uvicorn startet absichtlich erst nach erfolgreichem `upgrade head`.

### Frontend bleibt unhealthy oder API-Aufrufe schlagen fehl

```bash
docker compose logs frontend backend
```

Das Frontend wartet auf einen gesunden Backend-Healthcheck. Im Browser müssen Anfragen über `/api/backend/...` laufen; die interne Backend-Adresse darf nicht direkt im Browser verwendet werden.

### Worker ist degraded oder stale

```bash
docker compose logs worker
docker compose exec worker cat /var/lib/school-assistant/worker-status/worker-status.json
```

Prüfen Sie `DATABASE_URL`, den Schreibzugriff auf `worker-status` und ob `WORKER_HEALTH_MAX_AGE_SECONDS` größer als `WORKER_HEARTBEAT_INTERVAL_SECONDS` ist. Nur `running` plus `healthy` und ein frischer Heartbeat werden akzeptiert.

### Port ist belegt

```bash
docker compose ps
```

Stoppen Sie den kollidierenden Prozess oder den alten Stack. Die Ports bleiben absichtlich fest auf `3000`, `5432` und `8000` gebunden.

## Spätere Deployment-Schritte

Ein vollständiges Raspberry-Pi- oder Cloudflare-Deployment ist nicht enthalten. Vor einer Veröffentlichung müssen unter anderem ARM64-Builds, Ressourcenmessung, HTTPS/TLS, Secret-Verwaltung, Backups und Restore getestet werden. PostgreSQL darf nicht öffentlich oder durch einen Tunnel direkt freigegeben werden.
"# School-assistant" 
