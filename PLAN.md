# TODO

## Sofort

- [ ] README-Alembic-Stand korrigieren: Kapitel „Storage und Datenbank" nennt nur 2 Revisionen, real sind es 5 (`0003`, `0004`, `0005`)
- [ ] API-Tabelle im README um `/api/v1/files`-Zeilen ergänzen
- [ ] Doppelten Migrations-Start auflösen: `backend/Dockerfile` CMD vs. `backend-entrypoint.sh`
- [ ] Root-`.dockerignore` bei neuen Worker-Dateien mitpflegen und dokumentieren

## Automatischer WebUntis-Sync

- [ ] Scheduler-Job im Backend oder Worker (Sync läuft bisher nur per Button)
- [ ] Täglicher Sync je Benutzer mit aktiver WebUntis-Integration (4-Wochen-Fenster weiterverwenden)
- [ ] Idempotenz: kein Parallel-Lauf, `last_sync_at` beachten
- [ ] Fehler in `last_error` schreiben, Nutzerisolation wahren
- [ ] Tests mit gemocktem Adapter: planmäßiger Lauf, fehlende Felder, deaktivierte Integration

## Kalender-Integrationen (lesen und schreiben)

- [ ] iCalendar-Import: neues Integration-Kind `ical` im Katalog, Adapter, Sync-Endpoint
- [ ] Alembic-Migration `0006`: Quellen-Attribut an `CalendarEvent`
- [ ] iCalendar-Export: Änderungen aus der App als abonnierbare ICS-Quelle
- [ ] Google Kalender: OAuth 2.0, Termine lesen + anlegen/bearbeiten/löschen, bidirektional
- [ ] Apple Kalender: CalDAV mit App-spezifischem Passwort, lesen + schreiben
- [ ] Tokens/Passwörter verschlüsselt speichern (bestehendes `crypto.py`)
- [ ] Einstellungen-UI: Konfiguration, Verbindungstest, Sync-Button, OAuth-Rückkehr

## Desktop-App (Windows + macOS)

- [ ] Zielplattform festlegen: Tauri (Empfehlung) oder Electron
- [ ] Backend + SQLite/embedded Postgres statt Docker einbetten
- [ ] Signierte Installer: MSI/NSIS (Windows), DMG notarized (macOS)
- [ ] Auto-Update ohne Datenverlust
- [ ] Systray, Auto-Start, Offline-Verhalten

## Lokale KI-Modelle für Lernplan-Erstellung

- [ ] Modell-Katalog im Backend (Hugging Face/GGUF, mit Größe und RAM-Bedarf)
- [ ] Download-Manager in der App (fortsetzbar, SHA256-verifiziert)
- [ ] Lokale Inference (z. B. llama.cpp), ohne Docker
- [ ] Fine-Tuning-Pipeline für Lernplan-Modelle (lokal feinabgestimmt)
- [ ] Lernplan-Erstellung: Stundenplan + offene Aufgaben + Klausuren → Lernplan
- [ ] Fallback auf Online-Provider, Herkunft im UI (`local`/`provider`)

## Sprach-Assistent (STT + TTS)

- [ ] Lokales STT-Modell (Whisper-Derivat) mit Download über Modell-Katalog
- [ ] Lokales TTS-Modell (Piper), Sprachauswahl
- [ ] Mikrofonaufnahme im Frontend, Transkription im Backend
- [ ] Antwort per Sprachausgabe, Push-to-Talk/Tastenkürzel
- [ ] Fallback auf Textmodus ohne Modell

## Backup-Runner

- [ ] `pg_dump` nach `BACKUP_SCHEDULE`, Rotation nach `BACKUP_RETENTION_DAYS`
- [ ] Im Worker umsetzen, `last_backup` ins Status-JSON
- [ ] Restore-Test dokumentieren, Status in Settings-UI
- [ ] Backup-Pfad für Desktop-Variante anpassen

## Dokumente durchsuchbar (RAG)

- [x] `documents`-Sync: Ordner-Scan + Upload über `/api/v1/files`
- [x] Chunking + Embeddings (KI-Provider oder lokales Modell), pgvector-Suche
- [x] Antworten mit Quellenangaben
- [x] KI-Gateway-Platzhalter im Dashboard durch echten Assistenten ersetzen

## Deployment

- [ ] CI-Pipeline (Lint, Typcheck, Tests, Builds)
- [ ] ARM64-Buildcheck (Raspberry Pi)
- [ ] HTTPS/TLS, `SESSION_COOKIE_SECURE`, Reverse-Proxy
- [ ] Secret-Rotation + Restore-Übung