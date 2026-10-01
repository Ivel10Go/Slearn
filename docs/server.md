# Server-Betrieb (Docker Compose)

Betrieb als Docker-Compose-Stack: Einrichtung, Worker, Speicher, Backups, HTTPS, Secret-Rotation und Fehlersuche. Der Kurzeinstieg steht im [README](../README.md).

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
  | schlanker DB-Health-/Heartbeat-Worker, sichert per pg_dump
```

- `frontend` läuft als Next.js-Standalone-Image mit Node.js 22.
- Browseranfragen verwenden den same-origin-Pfad `/api/backend/*`.
- Der Next.js-Route-Handler unter `app/api/backend/[...path]/route.ts` leitet diese Anfragen serverseitig an `BACKEND_INTERNAL_URL` weiter. Im Compose-Stack ist das Ziel fest `http://backend:8000`; der Browser erhält diese interne URL nicht.
- `backend` startet ausschließlich `app.main:app` auf Port 8000.
- `worker` enthält nur Worker-Code und die Abhängigkeit `psycopg[binary]`; `pg_dump` kommt als Systembinary `postgresql-client-15` aus demselben Debian, aus dem das Image ohnehin besteht. Der Worker führt Datenbank-Healthchecks, Status-Heartbeats und den Backup-Runner aus. Es gibt keinen `from app...`-Import: die beiden Python-Bereiche teilen keinen Code.
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

## Worker

Der Worker ist absichtlich klein und besitzt keinen HTTP-Port und kein Backend-Image. Er verbindet sich direkt mit psycopg, führt `SELECT 1` aus, schreibt einen nicht geheimen Status-Heartbeat und sichert die Datenbank mit `pg_dump`. Der Code bleibt Standardbibliothek plus `psycopg`; es gibt keinen `from app...`-Import, weil die beiden Python-Bereiche sonst gemeinsam gepflegt werden müssten.

Der Docker-Healthcheck ist bewusst strikt: Nur die Kombination

```text
status == "running"
database == "healthy"
```

und ein nicht veralteter, zeitzonenbehafteter Heartbeat gilt als gesund. `degraded`, `starting`, `stopping`, `stopped`, `unchecked`, `unhealthy` und fehlende oder ungültige Heartbeats sind unhealthy.

Der Backup-Runner fasst `status` und `database` nie an. Ein fehlgeschlagener Dump ist ein anderes Problem als eine nicht erreichbare Datenbank, deshalb bleibt der Worker auch dann gesund. Der Healthcheck wertet genau drei Felder aus; der neue Block `backup` ist additiv und kann die Strenge nicht aufweichen. Das ist mit einem Test festgeschrieben (`worker/tests/test_backup.py`, `test_a_failed_backup_keeps_the_worker_healthy`).

Worker-Status:

```bash
docker compose logs -f worker
docker compose exec worker cat /var/lib/school-assistant/worker-status/worker-status.json
```

Die Datei liegt im eigenen Named Volume `worker-status`. Sie enthält Prozessstatus, PID, Heartbeat, letzte Datenbankprüfung und nur den Fehlertyp. Der Block `backup` ergänzt den Ablauf des Backup-Runners: `enabled`, `schedule`, `timezone`, `retention_days`, `directory_writable`, `pg_dump_path`, `runner_available`, `last_started_at`, `last_finished_at`, `last_successful_at`, `next_due_at`, `last_duration_seconds`, `last_file`, `last_size_bytes`, `kept_files`, `removed_files` und `last_error`. Alle Zeitstempel sind zeitzonenbehaftete UTC-Angaben mit `+00:00`; ohne Zone wären sie für den Healthcheck und die Anzeige wertlos. Kein Feld dieser Datei enthält ein Passwort. `pg_dump_path` nennt den Pfad des Programms, `pg_dump` selbst erscheint nirgends - es steht in `PGPASSWORD` in der Umgebung des Kindprozesses, nicht in der Argumentliste.

Sicherheits- und Mount-Regeln:

- Image und Compose laufen mit UID/GID `10001:10001`.
- Das Root-Dateisystem ist read-only; beschreibbar sind nur das eigene Statusverzeichnis und das Backup-Verzeichnis.
- Alle Linux-Capabilities werden entfernt und `no-new-privileges` ist aktiv.
- Dokumente und Importe sind im Worker read-only eingebunden.
- **Backups müssen im Worker read/write eingebunden sein**, sonst schlägt jeder Lauf mit `PermissionError` fehl. Der Worker prüft das beim Start und schreibt `directory_writable: false` in den Status.
- Der Build-Kontext enthält ausschließlich `worker/`.

## Storage und Datenbank

| Host-Pfad-Variable | Standardpfad | Backend | Worker |
| --- | --- | --- | --- |
| `DOCUMENTS_HOST_PATH` | `./data/documents` | read/write | read-only |
| `BACKUPS_HOST_PATH` | `./data/backups` | read/write | read/write, nur für `pg_dump` |
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
- `20260925_0003_integration_credentials` legt die verschlüsselten Zugangsdaten der Integrationen an.
- `20260925_0004_add_fields` ergänzt `subjects.short_name` und `lessons.is_cancelled`.
- `20260925_0005_greeting_cache` hält den Willkommenssatz je Nutzer und Tag.
- `20260929_0006_task_manager` ergänzt Priorität und Fälligkeit an Lernaufgaben und legt `study_task_series` sowie `study_task_dependencies` an. Die Änderungen an `study_plan_tasks` laufen über `batch_alter_table`, weil SQLite kein `ALTER TABLE ... ADD CONSTRAINT` kennt.
- `20260930_0007_grades` legt `grades` an. `exam` wird dafür **nicht** erweitert, es behält Titel, Datum und Quelle.

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

Der Backup-Runner ist umgesetzt und läuft im Worker; der Abschnitt "Backup und Restore" beschreibt ihn und den manuellen Weg daneben. Die Kalenderintegrationen bleiben reserviert.

## Backup und Restore

### Der automatische Runner

Der Worker sichert die Datenbank selbst. Er ist der einzige Dauerläufer, der nichts vom HTTP-Prozess abhängt, und `pg_dump` braucht weder FastAPI noch SQLAlchemy noch eine Session. Im Backend wäre ein Thread im Uvicorn-Prozess derselbe Fallstrick wie beim WebUntis-Scheduler: mit `uvicorn --workers 2` liefe die Sicherung zweimal.

```text
Worker ──► pg_dump --format=custom --no-owner --no-privileges ──► $BACKUP_STORAGE_PATH
```

- **Ablage:** `BACKUP_STORAGE_PATH`, im Compose-Stack `/var/lib/school-assistant/backups`, auf dem Host `BACKUPS_HOST_PATH`.
- **Zeitplan:** `BACKUP_SCHEDULE`, Standard `0 2 * * *`, ausgewertet in `SCHOOL_TIMEZONE`.
- **Aufbewahrung:** `BACKUP_RETENTION_DAYS`, Standard `30`, gezählt in **Tagen**.
- **Dateiname:** `school-<JJJJMMTTThhmmssZ>.dump` (UTC, lexikografisch sortierbar) plus `school-<...>.dump.sha256` im Format von `sha256sum -c`.

Warum der Zeitplan ein Cron-String bleibt und kein Intervall in Sekunden: `BACKUP_SCHEDULE` ist als `0 2 * * *` dokumentiert, und diese Schreibweise nennt einen Tageszeitpunkt, den man sich merken kann. Ausgewertet wird er **im Worker selbst**, nicht von einem Host-Cron - die Deutung hängt damit an keinem Betriebssystem und keinem Daemon. Unterstützt ist bewusst nur eine kleine, eindeutige Teilmenge, alles andere wird beim Start mit deutschem Klartext abgelehnt, statt stillschweigend falsch gedeutet zu werden:

| Feld | Werte | Bedeutung |
| --- | --- | --- |
| Minute | `*`, `N`, `N-M`, `N-M/S`, `*/S`, Kommalisten | 0 bis 59 |
| Stunde | wie oben | 0 bis 23 |
| Tag des Monats | wie oben | 1 bis 31 |
| Monat | wie oben | 1 bis 12 |
| Wochentag | wie oben | 0 bis 6, Sonntag ist 0; 7 wie 0 |

Nicht unterstützt und deshalb ein Fehler: `@daily` und andere Kurzformen, Monats- und Wochentagsnamen, `?`, `L`. Für `Tag des Monats` und `Wochentag` gilt die POSIX-Regel: sind **beide** Felder eingeschränkt, genügt eines von beiden. `0 2 * * 1-5` ist also "werktags um 02:00" und nicht die unmögliche UND-Deutung.

Nicht im Worker ausgeführt wird eine Cron-Bibliothek. Eine eingezogene Abhängigkeit wäre eine dritte Fremdbibliothek in einem Image, das sonst nur `psycopg` kennt, und sie zöge `python-dateutil` mit. Die Teilmenge hier ist eng genug, um sie in Tests festzuschreiben (`worker/tests/test_backup.py`, `ScheduleParsingTests`).

Der Runner verarbeitet jeden fälligen Termin höchstens einmal: gesucht wird der jüngste passende Zeitpunkt im Fenster `BACKUP_CATCHUP_HOURS` (Standard 72). War der Worker zum Termin nicht da, holt er ihn beim nächsten Start nach. Nach einem Neustart liest er den letzten erfolgreichen Lauf aus dem **Namen des neuesten Dumps** - eine eigene Zustandsdatei wäre eine zweite Wahrheit, die auseinanderlaufen kann.

Die Rotation löscht nur, was älter als `BACKUP_RETENTION_DAYS` ist, und **nie das neueste Backup**: bei `BACKUP_RETENTION_DAYS=0` bleibt genau eine Datei stehen. Das ist der Fall, in dem der Runner lange ausgefallen war und deshalb am wichtigsten ist. Gelöscht wird ausschließlich, was dem Namensmuster `school-<JJJJMMTTThhmmssZ>.dump` entspricht; eine von Hand abgelegte Datei wird weder rotiert noch als „letzter Lauf" gelesen. Eine **fehlgeschlagene** Sicherung löst keine Rotation aus - sonst räumt ein Ausfall die Historie weg.

Aus dem Worker-Log (ohne Zugangsdaten):

```text
Backup-Runner bereit: Zeitplan 0 2 * * * (Europe/Berlin), Aufbewahrung 30 Tage, Ablage /var/lib/school-assistant/backups
Backup-Ziel: postgres:5432/school als school_assistant
pg_dump: /usr/bin/pg_dump
Backup geschrieben: school-20260930T000001Z.dump (1048576 Byte, 2.3 s), 30 Sicherungen auf dem Datentraeger.
```

`Backup-Ziel` ist die Kontrollzeile: Der Host wird aus `DATABASE_URL` gelesen und **nie geraten oder auf `localhost` gesetzt**. Im Compose-Netz steht dort `postgres`, weil die URL im Compose-Stack auf den Service zeigt. Der Datenbankbenutzer braucht für `pg_dump` Rechte auf alle Tabellen und Sequenzen - `POSTGRES_USER` ist Superuser, genügt also.

### Was gesichert werden muss

`pg_dump` sichert ausschließlich die Datenbank. Hochgeladene Dokumente liegen als Dateien unter `DOCUMENT_STORAGE_PATH`; `documents.storage_path` verweigt auf diesen Pfad, und `app/api/files.py` schreibt die Datei unter einem UUID-Namen. Ein Backup, das nur die Datenbank kopiert, hinterlässt eine Datenbank, deren Dokumentenliste auf nicht existierende Dateien zeigt.

| Inhalt | Ort | Folge bei fehlendem Backup |
| --- | --- | --- |
| Schema und Daten | Named Volume `pgdata` | Benutzer, Fächer, Stundenplan, Hausaufgaben, Klausuren, Kalender, Lernaufgaben, Integrationen |
| Dokumente | `DOCUMENTS_HOST_PATH`, im Container `/var/lib/school-assistant/documents` | hochgeladene Dateien samt Abschnitten und Vektoren |
| Importe | `IMPORTS_HOST_PATH` | nur Zwischendateien von Abgleichen |
| Worker-Status | Named Volume `worker-status` | nichts; der Worker schreibt die Datei beim Start neu |

`pgdata` und `worker-status` liegen im Docker-Volume-Verzeichnis des Hosts. Ein normales `docker volume ls` zeigt die Namen, aber nicht den Pfad; für ein Backup auf ein anderes Laufwerk müssen beide Volumes und das Host-Verzeichnis der Dokumente kopiert werden.

### Dump anlegen

Linux und macOS:

```bash
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > school.dump
```

PowerShell:

```powershell
cmd /c 'docker compose exec -T postgres pg_dump -U school_assistant -d school_assistant -Fc > school.dump'
```

Zwei Details, an denen ein Backup sonst stillschweigend kaputtgeht: `-T` verhindert, dass `docker compose exec` ein TTY anhängt und die Binärdaten verfälscht, und der PowerShell-Aufruf läuft bewusst über `cmd /c`, weil `>` in PowerShell 5.1 den Textstrom umkodiert und das Dump zerstört. `-Fc` erzeugt das komprimierte Custom-Format, das `pg_restore` und nicht `psql` liest. Anschließend `ls -l school.dump`: Ist die Datei kilobytegroß, wurde sie beschädigt.

### Der Dump des Runners prüfen

Der Runner legt seinen Dump unter `BACKUPS_HOST_PATH` ab und schreibt daneben eine Prüfsumme. Beide Schritte sind read-only für den Betrachter:

```bash
ls -l ./data/backups
docker run --rm -v "$PWD/data/backups:/backups:ro" -w /backups pgvector/pgvector:0.8.1-pg15-bookworm \
  sha256sum -c school-20260930T000001Z.dump.sha256
```

`pg_restore --list` zeigt den Inhalt, ohne etwas wiederherzustellen - die billigste Prüfung, ob das Custom-Format lesbar ist:

```bash
docker compose exec worker pg_restore --list /var/lib/school-assistant/backups/school-20260930T000001Z.dump
```

### Restore in den laufenden Stack

```bash
docker compose stop backend worker
docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists < school.dump
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT extname FROM pg_extension;"
docker compose start backend worker
```

Ein Dump des Runners liegt im Host-Verzeichnis `BACKUPS_HOST_PATH` und wird direkt in den Container gespeist:

```bash
docker compose stop backend worker
docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists \
  < ./data/backups/school-20260930T000001Z.dump
docker compose start backend worker
```

Der Runner ruft `pg_dump` mit `--no-owner` und `--no-privileges` auf. Das ist kein Zufall: Ohne diese Schalter trägt der Dump Eigentümer-Anweisungen, und die Restore-Übung spielt in eine Wegwerf-Datenbank mit einem **anderen** Benutzer zurück - der Restore würde an genau diesen Anweisungen scheitern, nicht an den Daten.

Der Ablauf ist absichtlich in dieser Reihenfolge: `pg_restore --clean` droppt Objekte, und ein laufendes Backend hält darauf offene Verbindungen. Nach dem Start prüft `docker compose logs backend`, ob Alembic etwas nachmigriert hat - der Dump enthält den Stand aus `alembic_version`. Eine Migration bedeutet, dass Dump und Code nicht zusammengehören. In der Extension-Liste muss `vector` stehen; fehlt sie, hilft nur `CREATE EXTENSION IF NOT EXISTS vector;` im Ziel. `postgres/init/001-vector.sql` wird ausschließlich bei einer frischen Initialisierung des Volumes ausgeführt und ist für den Restore nicht zuständig.

### Restore-Übung

Das Ziel ist nicht, den Dump zurückzuspielen, sondern festzustellen, ob er benutzbar ist, bevor es darauf ankommt. Die Übung läuft deshalb in einem Wegwerf-Container mit eigenem Volume und fasst `pgdata` nicht an. `docker compose down -v` ist dafür der falsche Weg, weil es genau dieses Volume löscht.

```bash
docker run -d --name restore-drill \
  -e POSTGRES_USER=drill -e POSTGRES_PASSWORD=drill -e POSTGRES_DB=drill \
  -v restore-drill-data:/var/lib/postgresql/data \
  pgvector/pgvector:0.8.1-pg15-bookworm

docker exec restore-drill pg_isready -U drill -d drill
cat school.dump | docker exec -i restore-drill pg_restore -U drill -d drill --clean --if-exists --exit-on-error
docker exec restore-drill psql -U drill -d drill -c "SELECT count(*) FROM users;"
docker exec restore-drill psql -U drill -d drill -c "SELECT extname FROM pg_extension;"
docker rm -f restore-drill
docker volume rm restore-drill-data
```

`--exit-on-error` ist in der Übung richtig und im Notfallrestore fragwürdig: In der Übung soll ein Fehler laut werden und nicht teilweise wiederhergestelltes Chaos hinterlassen. Prüfziele sind dieselben wie im Notfall: Benutzerzeilen vorhanden, `vector` aktiv, Alembic-Stand plausibel. Ergänzend einen der ältesten Dump-Sätze nehmen, weil ein frischer Dump nur beweist, dass das Werkzeug funktioniert, nicht die Historie. Aus dem automatisierten Ablauf ist dafür `BACKUPS_HOST_PATH` auf ein anderes Laufwerk zu setzen und `BACKUP_RETENTION_DAYS` so groß zu wählen, dass der betreffende Tag noch auf dem Datenträger liegt.

**Nicht ausgeführt.** In der Entwicklungsumgebung dieser Äbung läuft kein Docker-Daemon; weder Dump noch Restore wurden je durchgespielt, und `pg_dump` stand auf dem Host nicht zur Verfügung. Der Ablauf ist dokumentiert, nicht bestätigt.

Dasselbe gilt für den automatischen Runner: implementiert und über einen als `pg_dump` eingesetzten Attrappen abgesichert, aber nie gegen eine echte PostgreSQL-Instanz gelaufen.

### Der Backup-Stand in den Einstellungen

`GET /api/v1/backups/status` liest den Block `backup` aus der Statusdatei des Workers und ist rein lesend; einen Knopf zum Auslösen gibt es nicht, weil der Zeitplan im Worker läuft und ein zweiter Weg dorthin ein zweiter Zustand wäre. Die Karte **Sicherung** in den Einstellungen zeigt Zeitplan, Aufbewahrung, letzte und nächste Sicherung, Dateiname, Größe, Anzahl der Sicherungen und den letzten Fehler.

Ist die Datei nicht erreichbar, antwortet der Endpunkt mit HTTP 200, `available: false` und einem deutschen Klartext - **kein** 503 und **kein** erfundenes Ergebnis. Zeitstempel ohne Zeitzone werden verworfen, statt sie als UTC zu deuten.

Damit das überhaupt etwas liefert, müssen Backend und Worker das Volume `worker-status` teilen. **Das ist im aktuellen Compose-Stand nicht der Fall:** Das Volume hängt nur am Worker, das Backend sieht die Datei also nicht und die Karte zeigt dauerhaft den Klartext. Die nötige Änderung ist ein Mount im Backend-Service:

```yaml
    volumes:
      - type: volume
        source: worker-status
        target: /var/lib/school-assistant/worker-status
        read_only: true
```

`WORKER_STATUS_PATH` ist für das Backend bereits vorhanden und zeigt auf denselben Pfad wie beim Worker.

### Ablageort außerhalb des Containers

`BACKUP_STORAGE_PATH` und `BACKUP_FILE_PREFIX` sind gewöhnliche Konfigurationswerte; im Image ist nichts davon fest verdrahtet. Das ist der Punkt, an dem eine Desktop-Installation andockt: Der Worker sucht `pg_dump` über `BACKUP_PG_DUMP_BINARY`, danach über den PATH und zuletzt unter `/usr/lib/postgresql/<version>/bin/`. Auf Windows genügt also ein Verweis auf die `pg_dump.exe` der mitgelieferten PostgreSQL-Installation, auf macOS einer auf den `PATH` des Startskripts. Am Worker-Code ändert sich dafür nichts.

Was **heute** schon sinnvoll ist und was erst mit einer Desktop-Variante Bedeutung bekommt, ist bewusst getrennt:

| Heute schon umgesetzt | Erst mit der Desktop-Variante |
| --- | --- |
| Ablageort aus `BACKUP_STORAGE_PATH`, nicht fest verdrahtet | Ablage unter einem Benutzerdatenverzeichnis je Betriebssystem |
| Datenbankhost aus `DATABASE_URL`, nie geraten und nie fest auf `localhost` | `localhost` bzw. der eingebettete Datenbankserver als bewusste Wahl |
| `pg_dump` über Suchpfad statt über einen festen Containerpfad | mitgeliefertes `postgresql-client` neben der eingebetteten Datenbank |
| Rotation in Tagen, unabhängig vom Ablageort | Abbruch bei zu wenig freiem Speicher, zweiter Datenträger |
| identischer Zeitplan- und Retention-Code | ein Tray-Eintrag, der den nächsten Termin anzeigt |

Die Desktop-App (Electron, siehe [Desktop-App](desktop.md)) nutzt diesen Runner nicht: sie arbeitet mit SQLite, gesichert wird dort der Datenordner.

## Betrieb hinter HTTPS

Ausgangslage: nichts eingerichtet. Der Stack terminiert kein TLS, und alle Compose-Portbindungen liegen auf `127.0.0.1`. Zertifikate, ACME und Reverse-Proxy gehören nicht in dieses Repository. Dieser Abschnitt beschreibt den Aufbau und die Stellen, an denen diese Anwendung anders reagiert als man zunächst erwartet.

### Beispiel: Caddy als Reverse-Proxy

Das folgende Beispiel ist **nicht eingerichtet und nicht Teil des Stacks**. Es ist eine kopierbare Vorlage für einen Betrieb hinter HTTPS.

```caddyfile
# Caddyfile -- BEISPIEL, im Repository bewusst nicht vorhanden.
#
# Voraussetzungen:
#   - ein DNS-Eintrag zeigt auf den Host, Ports 80 und 443 sind frei
#   - der Compose-Stack läuft, das Frontend antwortet auf 127.0.0.1:3000
#   - Caddy läuft als eigener Dienst auf demselben Host, nicht im Stack

school.example.de {
    # Caddy bezieht das Zertifikat selbst (ACME) und leitet HTTP auf HTTPS um.
    # reverse_proxy überträgt WebSocket-Upgrades und setzt X-Forwarded-*.
    reverse_proxy 127.0.0.1:3000
}
```

Ein nginx-Setup ist ebenso möglich; der Unterschied liegt nur in Zertifikatsverwaltung und Weiterleitung. Nicht weiterleiten dürfen `5432` (PostgreSQL) und `8000` (Backend) -- beide bleiben an `127.0.0.1` gebunden.

### `SESSION_COOKIE_SECURE`

Hinter TLS muss `SESSION_COOKIE_SECURE=true` gesetzt sein. Das Setting existiert bereits als `cookie_secure` in `app/core/config.py` und akzeptiert `SESSION_COOKIE_SECURE` ebenso wie den älteren Namen `COOKIE_SECURE`. Es ist **absichtlich nicht** an `APP_ENV=production` gebunden: derselbe Compose-Stack wird lokal und in Tests im Klartext benutzt, und ein automatisch gesetztes `Secure`-Flag führt dort zu einer Anmeldung, die scheinbar funktioniert und dann bei jeder Folgeanfrage abgewiesen wird, weil der Browser das Cookie gar nicht zurücksendet. Fehlt die Bindung, bleibt es eine Konfigurationsaufgabe; deshalb steht sie hier und in `.env.example` ausdrichtig dabei.

Der Fehler, an dem man das merkt, ist unspektakulär: `POST /api/v1/auth/login` antwortet mit 200 und `Set-Cookie`, jedes `/api/backend/v1/...` danach mit 401. In den Netzwerk-Elementen des Browsers ist das Cookie markiert, aber nicht vorhanden.

### Was der Reverse-Proxy für diese Anwendung nicht ändern muss

- `app/main.py` installiert weder `ProxyHeadersMiddleware` noch `TrustedHostMiddleware`. Das ist hier unkritisch: Das Session-Cookie wird in `app/api/auth.py` mit einem expliziten `secure=settings.cookie_secure` gesetzt, und keine Route bildet eine absolute URL aus `request.url.scheme`. Sobald das Backend Umleitungen oder absolute URLs erzeugt, ist `ProxyHeadersMiddleware` die Stelle, an der das nachgezogen werden muss.
- `SESSION_COOKIE_SAMESITE=lax` genügt. Der Browser spricht ausschließlich mit dem Frontend-Origin, alle API-Anfragen laufen same-origin über `/api/backend/*`. `none` ist nur für eine echte Cross-Site-Einbettung nötig und setzt `Secure` voraus.
- `CORS_ORIGINS` wird im Normalbetrieb nicht gebraucht, weil der Browser das Backend nie direkt erreicht. Nur wer `/api/v1/...` direkt aus dem Browser aufruft, trägt dort die HTTPS-Origin ein.
- Der Service Worker (`public/sw.js`, network-first mit `/offline/` als Fallback) braucht keine Anpassung.

### Auf dem Handy installieren (PWA)

Es gibt keine native Handy-App; die Weboberfläche lässt sich aber als PWA auf den Startbildschirm legen. Voraussetzung ist eine **HTTPS-Adresse** (Abschnitt oben): über Klartext-HTTP im LAN gilt die Seite nicht als sichere Herkunft, der Service Worker wird nicht registriert und das Mikrofon bleibt gesperrt. Eine Ausnahme ist `localhost`, die das Handy aber nicht erreicht.

- **Android (Chrome):** Menü ⋮ → „App installieren" bzw. „Zum Startbildschirm hinzufügen".
- **iOS (Safari):** Teilen-Symbol → „Zum Home-Bildschirm". Nur Safari kann das; Chrome auf iOS nicht.
- Von unterwegs ohne öffentlichen Port: ein VPN (z. B. Tailscale oder WireGuard) auf den Server und dort HTTPS über den Reverse-Proxy. Der Stack selbst bleibt an `127.0.0.1` gebunden.
- Nach einer Änderung der App-Shell `CACHE_NAME` in `public/sw.js` anheben, sonst sieht das Handy die alte Oberfläche.

### Prüfliste nach der Umstellung

```bash
curl -si -X POST https://school.example.de/api/backend/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"..."}' | grep -i '^set-cookie'
```

Erwartet wird `Secure` im `Set-Cookie`. Danach im Browser: anmelden, Dashboard laden, ein Dokument suchen, eine Lernaufgabe starten, im Offline-Modus die Navigation prüfen.

**Nicht verifiziert.** In dieser Umgebung gibt es keinen Docker-Daemon und keinen Proxy; es wurde kein TLS-Terminierungslauf und keine Zertifikatsausstellung durchgespielt. Die Aussagen oben sind aus dem Code abgeleitet, nicht beobachtet.

## Secret-Rotation

Warum ein Runbook und keine Automatik: `SECRET_KEY` und `ADMIN_PASSWORD` wirken an zwei verschiedenen Stellen -- einmal im Backend-Prozess, einmal in der Datenbank. `POSTGRES_PASSWORD` wirkt an einer dritten, nämlich im bereits initialisierten Volume, in das Compose-Umgebungsvariablen nicht mehr hineingreifen. Eine Rotation, die nur `.env` anfasst, sieht aus wie ein Erfolg und bewirkt fast nichts.

| Geheimnis | Wirkung einer Rotation | Aufwand |
| --- | --- | --- |
| `SECRET_KEY` | macht alle laufenden Sessions ungültig, sonst nichts | gering, jeder meldet sich einmal neu an |
| `ADMIN_PASSWORD` | **nichts**, solange der Benutzer schon existiert | hoch, es gibt keinen API-Weg dafür |
| `POSTGRES_PASSWORD` | **nichts**, solange `pgdata` existiert | mittel, `ALTER ROLE` plus `.env` plus Neustart |
| `INTEGRATION_ENCRYPTION_KEY` | macht alle gespeicherten Zugangsdaten unlesbar | hoch, alle Integrationen neu eintragen |

### `SECRET_KEY`

Session-Tokens sind zustandslos: `create_session_token` in `app/core/security.py` bildet einen HMAC-SHA256 über die Nutzer-ID und die Ablaufzeit, es gibt keine Sessiontabelle. Ein Wechsel des Schlüssels invalidiert deshalb sofort jede bestehende Sitzung, ohne Rest in der Datenbank.

```bash
# Neuen Wert in .env eintragen, dann:
docker compose up -d backend
```

Der Worker und das Frontend brauchen den Schlüssel nicht.

### `ADMIN_PASSWORD`

Das ist die Falle, die am leichtesten übersehen wird. `ensure_bootstrap_user` in `app/services/auth.py` legt den Benutzer nur an, wenn es ihn noch nicht gibt; existiert er, werden im `else`-Zweig ausschließlich `is_admin` und `is_active` repariert. `password_hash` wird nie angefasst. Es gibt außerdem keinen Endpunkt zum Ändern eines Passworts -- die Auth-Routen kennen nur `login`, `logout` und `me`. Ein neues `ADMIN_PASSWORD` in `.env` ändert also nichts am Passwort eines vorhandenen Administrators.

Es gibt zwei Wege, beide manuell:

```bash
# 1. Hash mit exakt dem Code-Pfad erzeugen, den auch der Login benutzt.
docker compose exec backend python -c "from app.core.security import hash_password; print(hash_password('NEUES-PASSWORT'))"

# 2. Hash in der Datenbank setzen.
docker compose exec postgres psql -U school_assistant -d school_assistant \
  -c "UPDATE users SET password_hash='<HASH AUS SCHRITT 1>' WHERE username='admin';"
```

Schritt 2 statt in der Oberfläche ist nötig, weil es keinen Passwortdialog gibt. Der Hash darf nur mit diesem Aufruf erzeugt werden: die Argumente des Hasher in `app/core/security.py` (`time_cost=3`, `memory_cost=65536`, `parallelism=4`, `hash_len=32`, `salt_len=16`, `Type.ID`) sind an keinen anderen Stellen hinterlegt, und ein Hash aus einem Werkzeug mit anderen Parametern wird beim Login abgewiesen. `password_needs_rehash` existiert, wird aber nirgends aufgerufen.

Der Umweg über einen neuen `ADMIN_USERNAME` funktioniert nur halb: Der Bootstrap legt dann einen zweiten Administrator an, der alte bleibt aktiv. Beide existieren anschließend, und der alte muss in der Datenbank deaktiviert werden.

### `POSTGRES_PASSWORD`

`POSTGRES_USER`, `POSTGRES_PASSWORD` und `POSTGRES_DB` werden von den Entrypoint-Skripten des `postgres`-Images nur bei der **Initialisierung** des Volumes ausgewertet. Ein bestehendes `pgdata` behält den alten Benutzer und das alte Passwort. `docker compose up -d` mit geändertem `.env` liefert dann `password authentication failed` und sonst nichts.

```bash
docker compose exec postgres psql -U school_assistant -d school_assistant \
  -c "ALTER USER school_assistant WITH PASSWORD 'NEUES-PASSWORT';"
```

Danach `POSTGRES_PASSWORD` **und** `DATABASE_URL` in `.env` aktualisieren; das Passwort muss in der URL kodiert sein. Erst dann `docker compose up -d`. Der laufende Backend-Prozess verbindet sich nur beim Start neu, deshalb reicht ein Neustart der Container.

### `INTEGRATION_ENCRYPTION_KEY`

Der Schlüssel wird per HKDF-SHA256 ohne Salt auf einen 32-Byte-AES-GCM-Schlüssel abgeleitet (`app/core/crypto.py`). Gespeichert wird ein Payload mit dem Präfix `sa1.`, gefolgt von Nonce und Chiffretext. Zwei Konsequenzen:

- Der Präfix enthält keine Schlüsselkennung. Es gibt keine Doppelkey-Logik und kein Werkzeug, das einen Bestand mit dem alten und dem neuen Schlüssel parallel lesbar hält. Nach dem Wechsel sind alle gespeicherten Zugangsdaten endgültig unlesbar, und `decrypt_credentials` wirft `CredentialDecryptionError`.
- Eine Rotation ist damit keine Schlüsselrotation, sondern eine Eingabeübung: Jede Integration muss in den Einstellungen neu angelegt oder mit Zugangsdaten aktualisiert werden. WebUntis-Zugang und KI-Schlüssel gehören in einen Passwortmanager, nicht in `.env`.

Diese Rotation ist deshalb nur bei Verdacht auf Kompromittierung sinnvoll. Sonst ist der Schaden größer als der Nutzen, weil ohne die alten Zugangsdaten kein Test und kein Stundenplanabgleich mehr möglich ist.

### Reihenfolge und Praxis

1. Einen Satz sicherer Ersatzwerte außerhalb des Hosts erzeugen und dort aufbewahren; die `.env` auf dem Host ist kein Tresor.
2. `SECRET_KEY` rotieren, weil es der günstigste Schritt ist und nur Sessions beendet.
3. `ADMIN_PASSWORD` über den Hashweg setzen, danach die Anmeldung prüfen.
4. `POSTGRES_PASSWORD` mit `ALTER USER` und passendem Eintrag in `DATABASE_URL`.
5. `INTEGRATION_ENCRYPTION_KEY` nur bei Verdacht, und nie zusammen mit einem Schritt, der Daten verliert.
6. `docker compose exec postgres psql -U ... -c "SELECT 1;"` als Gegenprobe, danach `docker compose ps`, weil ein stehengebliebener Healthcheck schlimmer ist als ein alter Schlüssel.

Ein Wert pro Schritt: Fällt nach `docker compose up -d` etwas aus, ist der Verdächtige der letzte Änderungskandidat. Nach jeder Rotation gehören Backup und Restore-Übung aus dem vorigen Abschnitt auf den Plan, weil `INTEGRATION_ENCRYPTION_KEY` und ein fehlgeschlagener Dump dieselbe Datenlage erzeugen.

## Security

- Compose besitzt keine Default-Passwörter und keinen Default-Secret-Key für Kernwerte.
- `.env`, `.env.*` und andere lokale Konfigurationen werden durch `.gitignore` ausgeschlossen; `.env.example` bleibt als dokumentierte Vorlage sichtbar.
- Alle Host-Portbindungen verwenden `127.0.0.1`; PostgreSQL ist nicht öffentlich erreichbar.
- Das Frontend erhält keine Datenbank-, Session- oder Admin-Secrets.
- `BACKEND_INTERNAL_URL` ist serverintern und darf nicht mit `NEXT_PUBLIC_` Präfix verwendet werden.
- `SESSION_COOKIE_SECURE=true` ist für eine Veröffentlichung über HTTPS erforderlich. Der Wert wird nicht aus `APP_ENV=production` abgeleitet, siehe "Betrieb hinter HTTPS".
- `CORS_ORIGINS` sollte nur tatsächlich benötigte Origin(s) enthalten.
- Der Worker läuft ohne Root-Rechte, ohne Linux-Capabilities, mit read-only Root-Dateisystem und read-only Datenzugriffen auf Dokumente und Importe.
- Automatische Backups laufen im Worker; **Restore-Tests und Secret-Rotation sind nicht eingerichtet** und ausgeführt wurde keiner der beiden Abläufe. Die Runbooks stehen in "Backup und Restore" und "Secret-Rotation".
- Eine spätere Veröffentlichung sollte TLS, Reverse-Proxy, Authentifizierung, Cookies, CORS, Rate-Limits, Backups und Log-Schutz gemeinsam prüfen. Die Prüfliste dafür beginnt im Abschnitt "Betrieb hinter HTTPS".

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

### Backup schlägt fehl

Ein Fehlschlag des Backups macht den Worker **nicht** unhealthy - der Healthcheck wertet nur `status`, `database` und den Heartbeat. Der eigentliche Fehler steht im Block `backup`:

```bash
docker compose logs worker | grep -i backup
docker compose exec worker cat /var/lib/school-assistant/worker-status/worker-status.json
```

Die vier wahrscheinlichsten Ursachen:

- `pg_dump wurde nicht gefunden` - das Image ist ohne `postgresql-client` gebaut worden. `docker compose build worker` behebt das.
- `Die Sicherung konnte nicht geschrieben werden: Permission denied` - der Backup-Pfad ist im Worker read-only eingebunden. Er muss read/write sein.
- `pg_dump endete mit Code 1: ...` - die Rolle aus `DATABASE_URL` darf die Datenbank nicht lesen, oder der Host stimmt nicht.
- Eine Zeile `Der Backup-Runner läuft nicht: ...` im Log nennt den Grund; ein unverständlicher `BACKUP_SCHEDULE` schaltet den Runner ab, statt ihn falsch zu deuten.

### Port ist belegt

```bash
docker compose ps
```

Stoppen Sie den kollidierenden Prozess oder den alten Stack. Die Ports bleiben absichtlich fest auf `3000`, `5432` und `8000` gebunden.

## Spätere Deployment-Schritte

Ein vollständiges Raspberry-Pi- oder Cloudflare-Deployment ist nicht enthalten. Die Bausteine dafür sind jetzt dokumentiert, aber keiner davon ist eingerichtet oder gelaufen: der Reverse-Proxy und `SESSION_COOKIE_SECURE` im Abschnitt "Betrieb hinter HTTPS", die Rotationsschritte im Abschnitt "Secret-Rotation", Dump und Restore-Übung im Abschnitt "Backup und Restore", der ARM64-Buildcheck als Workflow.

Offen bleibt, was sich in dieser Umgebung nicht prüfen lässt: Ressourcenmessung auf dem Zielgerät, ein durchgelaufener Restore, ein TLS-Handshake und ein Stack auf echter ARM-Hardware. PostgreSQL darf nicht öffentlich oder durch einen Tunnel direkt freigegeben werden.

Für den Backup-Runner kam hinzu: `postgresql-client-15` wurde nie per `apt-get` installiert, es gab keinen laufenden Stack und kein echtes `pg_dump`. Geprüft sind damit nur der Ablauf um das Programm herum (Pipe, Prüfsumme, atomares Umbenennen, Rotation, Nachholen nach einem Neustart) und das strenge Ablehnen unbekannter Zeitpläne. Ob der Paketname in `python:3.12-slim-bookworm` so heißt und ob `pg_dump` dort unter `/usr/bin` oder nur unter `/usr/lib/postgresql/15/bin` liegt, zeigt erst `docker compose build worker`.
