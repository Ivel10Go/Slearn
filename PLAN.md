# TODO

## Sofort

- [x] README-Alembic-Stand korrigieren: war bereits erledigt, alle 6 Revisionen sind in `README.md:268-273` gelistet
- [x] API-Tabelle im README um `/api/v1/files`-Zeilen ergänzen
- [x] Doppelten Migrations-Start auflösen: `backend/Dockerfile` CMD vs. `backend-entrypoint.sh` — Quelle der Wahrheit ist jetzt `backend/entrypoint.sh` (aus dem Root per `git mv` dorthin), Compose nutzt den Image-Default
- [x] Root-`.dockerignore` bei neuen Worker-Dateien mitpflegen und dokumentieren — Reihenfolge ist last-bearing, Tool-Caches werden jetzt hinter `!worker/**` wieder ausgeschlossen

## Automatischer WebUntis-Sync

- [x] Scheduler-Job im Backend oder Worker (Sync läuft bisher nur per Button) — `app/services/integration_scheduler.py`, Thread im Backend-Lifespan; der Worker bleibt Healthcheck-only
- [x] Täglicher Sync je Benutzer mit aktiver WebUntis-Integration (4-Wochen-Fenster weiterverwenden) — `WEBUNTIS_SYNC_ENABLED` / `WEBUNTIS_SYNC_INTERVAL_SECONDS`
- [x] Idempotenz: kein Parallel-Lauf, `last_sync_at` beachten — sitzt nur im Scheduler (`scheduled_sync_is_due()`), damit der manuelle Knopf immer greift
- [x] Fehler in `last_error` schreiben, Nutzerisolation wahren — `run_integration_sync()` liefert ein Ergebnis statt einer Exception
- [x] Tests mit gemocktem Adapter: planmäßiger Lauf, fehlende Felder, deaktivierte Integration — 21 neue Tests in `backend/tests/test_backend.py`

## Kalender-Integrationen (lesen und schreiben)

- [x] iCalendar-Import: neues Integration-Kind `ical` im Katalog, Adapter, Sync-Endpoint — `app/services/ical.py` ohne neue Dependency
- [x] Alembic-Migration `0006`: Quellen-Attribut an `CalendarEvent` — das Attribut (`sync_source_id`, `source`, `external_id`) gab es seit `0001`; neu ist `20260930_0008_calendar_source` mit `sync_state` + `remote_etag` für den Abgleich in beide Richtungen
- [x] iCalendar-Export: Änderungen aus der App als abonnierbare ICS-Quelle — `GET /api/v1/calendar/feed/{token}` mit widerrufbarem Token, ohne Session
- [x] Google Kalendar: OAuth 2.0, Termine lesen + anlegen/bearbeiten/löschen, bidirektional — **nur gegen gemockte HTTP-Antworten getestet**, kein Google-Konto vorhanden
- [x] Apple Kalender: CalDAV mit App-spezifischem Passwort, lesen + schreiben — **nur gegen gemockte Antworten getestet**
- [x] Tokens/Passwörter verschlüsselt speichern (bestehendes `crypto.py`) — `client_secret`/`refresh_token` als Credentials verschlüsselt, **nicht** in der Klartext-`configuration`-Spalte; Access-Token wird nie gespeichert
- [x] Einstellungen-UI: Konfiguration, Verbindungstest, Sync-Button, OAuth-Rückkehr — Schreiben erfordert `settings.preferences["share_calendar_for_writing"]`, Standard aus, jeder unlesbare Zustand bedeutet aus

## Desktop-App (Windows + macOS)

- [x] Zielplattform festlegen: Tauri (Empfehlung) oder Electron — Electron (`desktop/`)
- [x] Backend + SQLite/embedded Postgres statt Docker einbetten — PyInstaller-Backend mit SQLite
- [ ] Signierte Installer: MSI/NSIS (Windows), DMG notarized (macOS) — Workflow signiert, sobald die Secrets gesetzt sind; **Zertifikate fehlen noch**
- [x] Auto-Update ohne Datenverlust — `desktop/lib/updates.js`, Datenbanksicherung vor dem Update
- [x] Systray, Auto-Start, Offline-Verhalten

## Lokale KI-Modelle für Lernplan-Erstellung

- [x] Modell-Katalog im Backend (Hugging Face/GGUF, mit Größe und RAM-Bedarf) — `app/services/model_catalog.py`, statisch und kuratiert, fünf Einträge; Größen und SHA256 live gegen die Hugging-Face-API verifiziert. RAM-Bedarf wird **berechnet**, nicht gespeichert, und als Schätzung markiert
- [x] Download-Manager in der App (fortsetzbar, SHA256-verifiziert) — serverseitig in `app/services/model_downloads.py`: Resume per HTTP-Range, SHA256 über den wieder gelesenen Präfix, Prüfung **vor** dem atomaren Umbenennen, Fortsetzungspunkt aus `stat()` statt gespeichert
- [x] Lokale Inference (z. B. llama.cpp), ohne Docker — der Backend **startet keinen** Server; der Nutzer bringt seinen mit (`llama_cpp` in `RUNTIME_PRESETS`). Variante „Binary ins Image" hätte es um hunderte MB vergrößert und wäre auf dem unterstützten Raspberry Pi nicht lauffähig
- [ ] Fine-Tuning-Pipeline für Lernplan-Modelle (lokal feinabgestimmt)
- [x] Lernplan-Erstellung: Stundenplan + offene Aufgaben + Klausuren → Lernplan — Wochenplaner `app/services/planner.py`, `POST /api/v1/study/planner/{preview,apply}`, Karte oben auf `/lernen`. **Deterministisch, ohne Modell**; Migration `0015` verknüpft Lernblock und Hausaufgabe
- [x] Fallback auf Online-Provider, Herkunft im UI (`local`/`provider`) — Reihenfolge **lokal → online → der Aufrufer baut aus den Daten**, mit gestuftem Budget (15 s wenn ein Provider im Rückgrat ist, sonst 60 s); `source` bleibt `ai` gültig und wird über `normalize_source` zu `provider`
- [ ] „Modelle lernen am Nutzer": feinabgestimmtes Modell (LoRA-Adapter) pro Nutzer statt geteiltem Prompt. Fakten bleiben im Prompt (Notizen, Dokumente, Noten); das Feintuning soll das *Verhalten* verbessern (AKTION/MERKE-Protokoll, Datumsangaben, Ton)
  - [x] Trainingsbeispiele sammeln, nur mit Freigabe (`train_on_conversations`, Standard aus) — Migration `0012`, `app/services/training.py`; Signale: eingetragen, verworfen, gemerkte Notiz gelöscht, Daumen. Das Urteil wird aus den Signalen gerechnet, nicht gespeichert
  - [x] Hardware-Prüfung: Phase 1 nur auf Apple Silicon ab 16 GB (MLX) oder NVIDIA ab 6 GB (CUDA) — `app/services/training_hardware.py`
  - [ ] Trainer als optionaler Download (eigenständiges Python + venv, feste Version und SHA-256), LoRA-SFT + DPO, Export nach `adapter.gguf`
  - [ ] Prüfung gegen zurückgehaltene Beispiele, `llama-server --lora`, Zurückwechseln auf den alten Adapter
  - [ ] Später: Training in der Cloud als Abo (eigene Freigabe, EU-Standort, Einwilligung der Eltern unter 16)
- [x] Custom-KI ohne Docker: lokale OpenAI-kompatible Server (Ollama, LM Studio) als erste Klasse in `ai_provider`, inkl. `host.docker.internal`-Hinweis im UI (siehe AGENTS.md) — `optional_config: ["runtime"]`, kein zweiter Adapterpfad; der Hinweis steht an vier sichtbaren Stellen

## Sprach-Assistent (STT + TTS)

- [x] Lokales STT-Modell (Whisper-Derivat) mit Download über Modell-Katalog — zwei Whisper-Einträge lagen schon im Katalog; `GET /api/v1/speech/capabilities` sagt je Richtung getrennt, ob **Modell** und ob **Laufzeit** vorhanden ist
- [x] Lokales TTS-Modell (Piper), Sprachauswahl — `backend/app/services/piper_voices.py` baut fertige `ModelSpec`-Objekte und lädt über **denselben** unveränderten Download-Manager. Piper-Stimmen sind zwei Einzeldateien (`.onnx` + `.onnx.json`), **kein Archiv** — deshalb wurde kein Entpacker gebaut
- [x] Mikrofonaufnahme im Frontend, Transkription im Backend — **zwei echte CSP-Blocker gefunden und behoben**: `Permissions-Policy: microphone=()` blockierte `getUserMedia` komplett, und das fehlende `media-src` ließ `default-src 'self'` gelten, was `blob:` sperrte (die **Wiedergabe** wäre gestorben, nicht die Aufnahme)
- [x] Antwort per Sprachausgabe, Push-to-Talk/Tastenkürzel — Strg/⌘ + Umschalt + L, gehalten; `feuertKuerzel()` lässt es nicht feuern bei Eingabefeldern, laufender IME-Komposition oder gedrückter Alt-Taste
- [x] Fallback auf Textmodus ohne Modell — **erste Klasse, nicht Fehlerzweig**: `GET /capabilities` antwortet immer 200 und nennt pro Richtung einen deutschen Grund, halbe Ausstattung funktioniert halb, ein verweigertes Mikrofon ist `mikrofonZustand: "verweigert"` ohne Fehler

## Backup-Runner

- [x] `pg_dump` nach `BACKUP_SCHEDULE`, Rotation nach `BACKUP_RETENTION_DAYS` — im Worker, `pg_dump` kommt als `postgresql-client-15` ins Image, Rotation nach Tagen löscht nie das neueste Backup
- [x] Im Worker umsetzen, `last_backup` ins Status-JSON — additiver `backup`-Block; die Healthcheck-Strenge (`status`/`database`/`last_heartbeat`) bleibt unverändert
- [x] Restore-Test dokumentieren, Status in Settings-UI — Runbook im README, `GET /api/v1/backups/status`. Benötigt den `worker-status`-Mount am Backend (nachgezogen)
- [x] Backup-Pfad für Desktop-Variante anpassen — Ablagepfad konfigurierbar; README trennt „heute schon umgesetzt" von „erst mit der Desktop-Variante"

## Dokumente durchsuchbar (RAG)

- [x] `documents`-Sync: Ordner-Scan + Upload über `/api/v1/files`
- [x] Chunking + Embeddings (KI-Provider oder lokales Modell), pgvector-Suche
- [x] Antworten mit Quellenangaben
- [x] KI-Gateway-Platzhalter im Dashboard durch echten Assistenten ersetzen
- [x] Datei-Upload und „KI-Assistent zum Fragen" (`POST /api/v1/documents/ask`) sind damit abgedeckt

## Deployment

- [x] CI-Pipeline (Lint, Typcheck, Tests, Builds) — `.github/workflows/ci.yml`, 7 Jobs; bekannte Baseline-Ausnahmen über `.github/ci/check-known-failures.mjs` allowlistet statt per `continue-on-error`
- [x] ARM64-Buildcheck (Raspberry Pi) — `.github/workflows/arm64.yml`, wöchentlich per QEMU; im PR nur der billige `pip download`-Check
- [x] HTTPS/TLS, `SESSION_COOKIE_SECURE`, Reverse-Proxy — `cookie_secure` existierte schon in `app/core/config.py` und ist an `APP_ENV=production` **nicht** gebunden; Pflicht jetzt in `.env.example`, Compose und README. Caddy-Beispiel im README, **nicht ausgeführt**
- [x] Secret-Rotation + Restore-Übung — Runbook im README, das die echten Fallen adressiert (`ADMIN_PASSWORD` rotiert nicht, `POSTGRES_PASSWORD` greift nicht auf ein initialisiertes `pgdata`-Volume, `INTEGRATION_ENCRYPTION_KEY` per HKDF)

## Task-System (Ausbau)

Das Grundsystem steht: `StudyPlanTask` mit `start`/`stop`/`complete` (`app/api/study.py`). Offen ist die Tiefe, nicht das Fundament.

- [x] Priorität und Fälligkeitsdatum an Aufgaben (`study_plan_tasks.priority` / `.due_date`, 0–5 bzw. UTC-Zeitpunkt)
- [x] Serientyp für wiederkehrende Aufgaben (Vokabeln, Wiederholungsblätter) — `study_task_series`, pro Abschluss genau eine Occurrence
- [x] Aufgaben aus Klausurthemen erzeugen statt sie nur zu verlinken — `POST /api/v1/exams/{id}/tasks`, nach Lernstand sortiert und verkettet
- [x] Abhängigkeiten zwischen Aufgaben, damit ein Lernplan eine Reihenfolge hat — `study_task_dependencies`, blockiert den Start bis zur offenen Voraussetzung
- [x] Lernpläne überhaupt verwaltbar machen: ohne `/api/v1/study/plans` war keine Aufgabe aus der Oberfläche anlegbar

## Noten

- [x] Alembic-Migration: eigenes `Grade`-Modell, `Exam` erweitert sich nicht (es hat heute nur Titel/Datum/Quelle) — `20260930_0007_grades`, `Exam` unangetastet
- [x] Felder: Note, Art (Klassenarbeit, Test, mündlich), Gewichtung, Datum, Fach, Kommentar — plus `source`/`external_id` für den Dokumentimport
- [x] Noten-UI im Dashboard: Schnitt je Fach und gesamt, Verlauf als schlichte Linie ohne Diagramm-Bibliothek — `components/dashboard/grades-card.tsx`, `polyline` von Hand in einem `viewBox`
- [x] Manuelle Erfassung und Übernahme aus einem abgegebenen Dokument — zweiseitiger Import, Schlüssel ist der Zeileninhalt, nicht die Zeilennummer
- [x] Noten in Lernplan und KI-Kontext einbeziehen, damit Prioritäten am Notenstand hängen — `exam_priority()`: **Reihenfolge bleibt die Konfidenz**, Priorität ist `max(konfidenz, notenstand)`; ohne Noten ergibt die Note 0, nicht 4
- [x] Hochrechnung bestanden/nicht bestanden statt nur Rückblick — liegt die errechnete Note außerhalb 1,0–6,0, ist sie `None` und nicht geklemmt
- [x] Eigenes Rechtemanagement: Noten nicht ungefragt in `documents/ask` oder an den Provider schicken — Freigabe in `settings.preferences["share_grades_with_ai"]`, **Standard aus**, jeder unlesbare Zustand bedeutet aus; genau ein Weg zum Provider (`grade_context_lines()`), und ein Widerruf löscht den heutigen `GreetingCache`-Eintrag

## Lernfortschritt und Lernprozess

- [x] `LearningProgress` ist heute nur per Demo-Seed beschrieben und wird nirgends geschrieben: eigener Endpoint für `confidence` und `last_reviewed_at` — `PUT /api/v1/progress/topics/{topic_id}` (Upsert, deshalb PUT). Keine Migration nötig, das Modell hatte alle Spalten
- [x] Lernprotokoll aus `StudySession` auswerten (Dauer je Fach, Wochentag), nicht nur anzeigen — `GET /api/v1/progress/insights`, Aggregation in der Nutzerzeitzone
- [x] Serien (Streaks) und Wochenziel, abgeleitet aus `StudySession` — Lerntag = Kalendertag mit ≥ 1 abgeschlossener Minute; Wochenziel in Minuten, Woche Montag–Sonntag, Default 300
- [x] Fortschrittskarte im Dashboard: Konfidenz, Lernzeit, nächste Wiederholung — `components/dashboard/progress-card.tsx`, eckig/monochrom wie der Rest
- [x] Wiederholungsplanung als einfaches Intervall, kein vollständiges SRS — Konfidenz bleibt manuell, sie wird nicht aus Sessions abgeleitet

## Benachrichtigungen

- [x] `User.notifications_enabled` ist eine tote Spalte: verdrahten oder aus der Migration entfernen — **verdrahtet** als Hauptschalter; Kanäle/Uhrzeit/Ruhezeiten liegen in `settings.preferences`, ein einzelnes Boolean skaliert darauf nicht. Unlesbarer Zustand = aus
- [x] Push-Handler in `public/sw.js` ergänzen (bisher nur Caching, kein Push) und Cache-Namen bumpen — `lernraum-static-v6`; `push`/`notificationclick`/`message` sind additiv und fassen den Cache nicht an, VAPID-Schlüssel kommt per `postMessage`
- [x] Erinnerungen an fällige Lernaufgaben und Klausuren; Scheduler fehlt komplett, siehe WebUntis-Sync — `app/services/notification_scheduler.py`. **Bewusst ein zweiter Thread**, nicht im WebUntis-Scheduler: andere Taktart (5 min statt 24 h) und eigene Fehlergrenze (startet unabhängig von `WEBUNTIS_SYNC_ENABLED`)
- [x] Web-Push plus lokale Benachrichtigungen als Fallback für den Browser — der lokale Weg ist der **Hauptpfad**: offene Seite holt Zeilen über `GET /v1/notifications`, ein vom Dienst angenommener Push setzt `delivered_at` und nimmt die Zeile aus der lokalen Warteschlange
- [x] Einstellungen je Nutzer: Kanäle, Uhrzeit, Ruhezeiten — Ruhezeit in der Nutzerzeitzone, mit Zweig für die über Mitternacht laufende (`22:00–07:00`); in der Ruhezeit wird **nicht** zugestellt, der verschobene Moment wandert auf das Ruheende

## Reward-System

- [x] Punkte für erledigte Lernaufgaben und abgeschlossene Lernsessions — `reward_entries` als **Ledger vergüteter Ereignisse**, kein Zählerfeld; Eindeutigkeit über `(user_id, kind, event_key)`
- [x] Auszeichnungen in eigener Tabelle, nicht in der UI-`badge`-Komponente — `reward_badges` mit Bedingung, Fortschritt, Zeitpunkt und `scope`; die Badge-Komponente rendert nur einen fertigen Zustand, eine Auszeichnung ist ein Objekt mit Lebenslauf
- [x] Fortschrittsanzeige im Dashboard, im vorhandenen eckigen Monochrom-Design — `components/dashboard/rewards-card.tsx`, gleiche Spalte wie Fortschritt und Noten
- [x] Belohnungen an Lerninhalte koppeln, damit es kein reines Zähler-Spiel bleibt — Einheit ist der **Fachtag**, nicht die Aufgabe; gestaffelt statt Satz-pro-Minute, gedeckelt, und Punkte steigen monoton (kein Rückgabemechanismus)
- [x] Punkte niemals in Noten- oder KI-Berechnungen einfließen lassen — `app/services/rewards.py` importiert weder grades noch study noch KI; ein AST-Test prüft die Gegenrichtung, ein Wertobjekt-Test misst Priorität, Schnitt und Hochrechnung vor und nach dem Punktevergütung

