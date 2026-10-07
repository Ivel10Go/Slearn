# Lernraum

Lernraum ordnet den Schulalltag an einem Ort: Stundenplan, Hausaufgaben, Prüfungen, Termine, Lernpläne, Noten und Lernzeit. Auf Wunsch holt er den Stundenplan aus WebUntis und beantwortet Fragen zu deinen eigenen Unterlagen, mit einem KI-Dienst oder ganz ohne.

Es gibt Lernraum in zwei Formen:

| | Desktop-App | Server |
| --- | --- | --- |
| Für wen | eine Person an ihrem Rechner | selbst betrieben, z. B. auf einem Heimserver |
| Installation | Installer starten, Namen eingeben | Docker Compose |
| Anmeldung | keine | Benutzername und Passwort |
| Daten | SQLite im Benutzerordner | PostgreSQL mit pgvector |

## Desktop-App

**Installieren:** Den Installer für dein System von der [Release-Seite](https://github.com/Ivel10Go/School-assistant/releases/latest) herunterladen (`Lernraum-Setup-<version>.exe` für Windows, `Lernraum-<version>-<arch>.dmg` für macOS, `arm64` für Apple Silicon, `x64` für Intel) und starten. Beim ersten Start fragt Lernraum nur nach deinem Namen.

**Updates** lädt die App selbst über GitHub-Releases: Sie sieht beim Start und danach alle sechs Stunden nach, lädt eine neue Version im Hintergrund und installiert sie beim nächsten Neustart. Vorher sichert sie die Datenbank.

Ob ein Installer signiert ist, hängt davon ab, ob für den Release ein Zertifikat hinterlegt war. Ist er es nicht, gilt:

- **Windows:** Beim ersten Start *Weitere Informationen → Trotzdem ausführen* wählen.
- **macOS:** Die App einmal mit *Rechtsklick → Öffnen* starten. Automatische Updates funktionieren unter macOS nur bei einer signierten App; sonst die neue Version von der Release-Seite installieren.

**Deine Daten** liegen unter `%APPDATA%\Lernraum` (Windows) bzw. `~/Library/Application Support/Lernraum` (macOS). In der App findest du den Ordner unter *Einstellungen → Daten auf diesem Rechner*. Zum Sichern Lernraum beenden und den ganzen Ordner kopieren. Eine Deinstallation lässt die Daten liegen.

**Installer selbst bauen** (Node 22 und Python 3.12; jedes System baut seinen eigenen Installer):

```bash
cd frontend && npm ci
cd ../desktop && npm ci
npm run dist          # Ergebnis in desktop/dist/
```

Unter Windows-PowerShell `npm.cmd` statt `npm` verwenden. Details: [docs/desktop.md](docs/desktop.md).

## Server mit Docker

```bash
cp .env.example .env    # Windows: Copy-Item .env.example .env
```

In `.env` diese Werte ausfüllen: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `DATABASE_URL`, `SECRET_KEY`, `INTEGRATION_ENCRYPTION_KEY` und `ADMIN_PASSWORD`. Den `INTEGRATION_ENCRYPTION_KEY` danach nicht mehr ändern, sonst sind gespeicherte Zugangsdaten unlesbar.

```bash
docker compose up -d --build
```

Danach läuft Lernraum unter <http://localhost:3000>. Anmelden kannst du dich mit `ADMIN_USERNAME` (Standard `admin`) und `ADMIN_PASSWORD`.

Betrieb, Backups, HTTPS und Fehlersuche: [docs/server.md](docs/server.md). Alle Variablen: [docs/konfiguration.md](docs/konfiguration.md).

## Entwicklung

| Ordner | Inhalt |
| --- | --- |
| `frontend/` | Next.js 16, React 19, Tailwind v4 |
| `backend/` | FastAPI, SQLAlchemy, Alembic |
| `worker/` | Datenbank-Healthcheck und Backups |
| `desktop/` | Electron-Hülle der Desktop-App |
| `landing/` | Landingpage mit Warteliste, eigene Next.js-App ([docs/landing.md](docs/landing.md)) |

```bash
cd backend  && python -m pytest
cd frontend && npm test && npm run lint && npm run typecheck
cd desktop  && npm run check
```

Mehr dazu: [docs/entwicklung.md](docs/entwicklung.md) für Tests, Builds und CI, [docs/api.md](docs/api.md) für die API-Referenz und [AGENTS.md](AGENTS.md) für die Stolperfallen je Bereich.
