# TODO

## Sofort

- [x] README-Alembic-Stand korrigieren: war bereits erledigt, alle 6 Revisionen sind in `README.md:268-273` gelistet
- [x] API-Tabelle im README um `/api/v1/files`-Zeilen ergänzen
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
- [ ] „Modelle lernen am Nutzer": persönlicher Trainingsdatensatz je Benutzer (eigene Noten, Aufgaben, hochgeladene Materialien), feinabgestimmtes Modell pro Nutzer statt geteiltem Prompt
- [ ] Custom-KI ohne Docker: lokale OpenAI-kompatible Server (Ollama, LM Studio) als erste Klasse in `ai_provider`, inkl. `host.docker.internal`-Hinweis im UI (siehe AGENTS.md)

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
- [x] Datei-Upload und „KI-Assistent zum Fragen" (`POST /api/v1/documents/ask`) sind damit abgedeckt

## Deployment

- [ ] CI-Pipeline (Lint, Typcheck, Tests, Builds)
- [ ] ARM64-Buildcheck (Raspberry Pi)
- [ ] HTTPS/TLS, `SESSION_COOKIE_SECURE`, Reverse-Proxy
- [ ] Secret-Rotation + Restore-Übung

## Task-System (Ausbau)

Das Grundsystem steht: `StudyPlanTask` mit `start`/`stop`/`complete` (`app/api/study.py`). Offen ist die Tiefe, nicht das Fundament.

- [x] Priorität und Fälligkeitsdatum an Aufgaben (`study_plan_tasks.priority` / `.due_date`, 0–5 bzw. UTC-Zeitpunkt)
- [x] Serientyp für wiederkehrende Aufgaben (Vokabeln, Wiederholungsblätter) — `study_task_series`, pro Abschluss genau eine Occurrence
- [x] Aufgaben aus Klausurthemen erzeugen statt sie nur zu verlinken — `POST /api/v1/exams/{id}/tasks`, nach Lernstand sortiert und verkettet
- [x] Abhängigkeiten zwischen Aufgaben, damit ein Lernplan eine Reihenfolge hat — `study_task_dependencies`, blockiert den Start bis zur offenen Voraussetzung
- [x] Lernpläne überhaupt verwaltbar machen: ohne `/api/v1/study/plans` war keine Aufgabe aus der Oberfläche anlegbar

## Noten

- [ ] Alembic-Migration: eigenes `Grade`-Modell, `Exam` erweitert sich nicht (es hat heute nur Titel/Datum/Quelle)
- [ ] Felder: Note, Art (Klassenarbeit, Test, mündlich), Gewichtung, Datum, Fach, Kommentar
- [ ] Noten-UI im Dashboard: Schnitt je Fach und gesamt, Verlauf als schlichte Linie ohne Diagramm-Bibliothek
- [ ] Manuelle Erfassung und Übernahme aus einem abgegebenen Dokument
- [ ] Noten in Lernplan und KI-Kontext einbeziehen, damit Prioritäten am Notenstand hängen
- [ ] Hochrechnung bestanden/nicht bestanden statt nur Rückblick
- [ ] Eigenes Rechtemanagement: Noten nicht ungefragt in `documents/ask` oder an den Provider schicken

## Lernfortschritt und Lernprozess

- [ ] `LearningProgress` ist heute nur per Demo-Seed beschrieben und wird nirgends geschrieben: eigener Endpoint für `confidence` und `last_reviewed_at`
- [ ] Lernprotokoll aus `StudySession` auswerten (Dauer je Fach, Wochentag), nicht nur anzeigen
- [ ] Serien (Streaks) und Wochenziel, abgeleitet aus `StudySession`
- [ ] Fortschrittskarte im Dashboard: Konfidenz, Lernzeit, nächste Wiederholung
- [ ] Wiederholungsplanung als einfaches Intervall, kein vollständiges SRS

## Benachrichtigungen

- [ ] `User.notifications_enabled` ist eine tote Spalte: verdrahten oder aus der Migration entfernen
- [ ] Push-Handler in `public/sw.js` ergänzen (bisher nur Caching, kein Push) und Cache-Namen bumpen
- [ ] Erinnerungen an fällige Lernaufgaben und Klausuren; Scheduler fehlt komplett, siehe WebUntis-Sync
- [ ] Web-Push plus lokale Benachrichtigungen als Fallback für den Browser
- [ ] Einstellungen je Nutzer: Kanäle, Uhrzeit, Ruhezeiten

## Reward-System

- [ ] Punkte für erledigte Lernaufgaben und abgeschlossene Lernsessions
- [ ] Auszeichnungen in eigener Tabelle, nicht in der UI-`badge`-Komponente
- [ ] Fortschrittsanzeige im Dashboard, im vorhandenen eckigen Monochrom-Design
- [ ] Belohnungen an Lerninhalte koppeln, damit es kein reines Zähler-Spiel bleibt
- [ ] Punkte niemals in Noten- oder KI-Berechnungen einfließen lassen

