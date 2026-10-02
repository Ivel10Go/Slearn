# Desktop-App

Aufbau, Datenablage und Bau der Installer der Desktop-App für Windows und macOS.

Für Verbraucherinnen und Verbraucher gibt es Lernraum als Desktop-App, ohne Docker, Server und Anmeldung: Installer starten, Namen eingeben, fertig. Die App ist dieselbe Oberfläche wie im Browser; sie bringt Backend und Oberfläche selbst mit.

```text
Electron-Fenster ──http://127.0.0.1:47821──▶ Next-Standalone-Server (Electrons eigenes Node)
                                                └─▶ http://127.0.0.1:<frei> ──▶ FastAPI (PyInstaller) ──▶ SQLite
```

| Thema | Desktop-App | Compose-Stack |
| --- | --- | --- |
| Datenbank | SQLite-Datei im Benutzerordner | PostgreSQL 15 + pgvector |
| Anmeldung | keine (`AUTH_ENABLED=false`), Konto aus der Ersteinrichtung | Session-Cookie, Konto aus `ADMIN_*` |
| Dokumentsuche | Stichwortsuche (kein pgvector) | Vektor- und Stichwortsuche |
| Backups | Datenordner bei geschlossener App kopieren | `pg_dump`-Runner im Worker |
| Schlüssel | beim ersten Start erzeugt, `secrets.json` im Benutzerordner | `.env` |

**Wo die Daten liegen:** Windows `%APPDATA%\Lernraum\`, macOS `~/Library/Application Support/Lernraum/`. Darin `data/` (Datenbank, Dokumente, Modelle), `logs/` und `secrets.json`. Ohne `secrets.json` sind gespeicherte Zugangsdaten (WebUntis, KI-Dienst) nicht mehr lesbar – beim Sichern immer den ganzen Ordner kopieren. Eine Deinstallation lässt die Daten liegen. In der App: *Einstellungen → Daten auf diesem Rechner* bzw. Menü *Hilfe → Datenordner öffnen*.

## Installer bauen

Jeder Installer entsteht auf seinem eigenen System (PyInstaller kompiliert nicht quer). Voraussetzungen: Node 22 und Python 3.12.

```powershell
cd frontend; npm.cmd ci; cd ..\desktop
npm.cmd ci
npm.cmd run dist          # Windows: desktop\dist\Lernraum-Setup-<version>.exe
```

```bash
cd frontend && npm ci && cd ../desktop
npm ci
npm run dist              # macOS: desktop/dist/Lernraum-<version>-<arch>.dmg (Architektur des bauenden Macs)
```

`npm run dist` friert das Backend in einem eigenen venv ein (`desktop/.venv-backend`, nur `backend/requirements.txt`), baut die Oberfläche mit `next build --webpack` als Standalone-Server und packt beides mit electron-builder. `npm run smoke:backend` startet das eingefrorene Backend gegen eine leere Datenbank und prüft Migrationen, Ersteinrichtung und Dashboard. Der Workflow `.github/workflows/desktop.yml` baut alle drei Installer (Windows x64, macOS arm64, macOS x64) bei einem `v*`-Tag oder per Hand.

Die Installer sind **unsigniert**: Windows zeigt beim ersten Start einen SmartScreen-Hinweis (*Weitere Informationen → Trotzdem ausführen*), macOS verlangt einmal *Rechtsklick → Öffnen*. Für eine Verteilung ohne Warnung braucht es ein Codesigning-Zertifikat bzw. eine Apple-Developer-Mitgliedschaft (Notarisierung).

## Entwickeln

```powershell
cd desktop
npm.cmd ci
npm.cmd run build:frontend   # einmal, oder stattdessen ein laufendes `next dev`:
$env:LERNRAUM_FRONTEND_URL = "http://localhost:3000"
npm.cmd start                # startet Python-Backend direkt aus backend/ (LERNRAUM_PYTHON wählt den Interpreter)
```
