# AGENTS.md

Lokal betreibbarer Docker-Compose-Stack: Next.js 16 Frontend, FastAPI Backend, PostgreSQL 15 + pgvector, schlanker Python-Worker. `README.md` ist nur der Einstieg; die ausführliche Referenz liegt unter `docs/` (`server.md` Betrieb/Backup/HTTPS/Secret-Rotation, `api.md` API-Tabelle, `konfiguration.md` alle Env-Variablen, `entwicklung.md` Tests/CI, `desktop.md` Desktop-App) — hier nur schwer erkennbares Kontextwissen.

## Verzeichnisse

| Pfad | Inhalt |
| --- | --- |
| `frontend/` | Next.js 16 (App Router, Turbopack, React 19, Tailwind v4). Einziger npm-Package-Boundary. |
| `backend/` | FastAPI + SQLAlchemy + Alembic. Einziger Python-Package-Boundary, `app/` unter `backend/`. `entrypoint.sh` liegt hier, nicht im Root. |
| `worker/` | Nur DB-Healthcheck + Status-Heartbeat. Kein HTTP-Port, keine Jobs. |
| `desktop/` | Electron-Hülle der Desktop-App (Windows/macOS): startet das eingefrorene Backend und den Next-Standalone-Server, eigenes `package.json`. Siehe „Desktop-App“. |
| `postgres/init/` | pgvector-Aktivierung bei neuer DB. |
| `.github/` | Zwei Workflows (`ci.yml`, `arm64.yml`) plus der Baseline-Prüfer unter `ci/`. Kein Task-Runner, sonst nichts. |
| `data/` | Gitignoriert (bis auf `.gitkeep`). |

## Task-Manager (`feature/task-manager`)

Vier Planpunkte aus `PLAN.md` sind umgesetzt. Der Branch-Name ist inzwischen Historie — zwei weitere Wellen (planmäßiger WebUntis-Abruf, Lernfortschritt) sind danach gemergt worden —, aber die Notizen hier gelten unverändert. Das Verzeichnis, bevor hier jemand zum zweiten Mal ansetzt:

### Frontend-Architektur der Lernverwaltung

- **Die Verwaltung liegt auf `/lernen`, nicht im Dashboard.** Drei Karten: `study-plans-card` (Pläne), `study-series-card` (Serien), `study-tasks-card` (Aufgaben), zusammengesetzt in `app/lernen/page.tsx`. Das gilt für die *Verwaltung*. `progress-card` gehört **nicht** in die Verwaltung: sie ist der einzige Ort, an dem `LearningProgress` geschrieben wird, und eine Verwaltungskarte, die nur einen Zähler beobachtet, wäre eine tote. Sie steht seit dem Layout-Umbau mit Noten und Belohnung auf `/fortschritt` (`app/fortschritt/page.tsx`) — sie beantwortet „wie stehe ich?", und das Dashboard nur noch „was steht heute an?".
- **Die Planliste wird oben einmal geladen und nach unten gereicht.** Grund: Serien und Aufgaben brauchen dieselbe Liste für ihre Auswahlfelder. Drei eigene Ladevorgänge wären drei Anfragen, und nach dem Anlegen eines Plans wüssten die anderen Karten den neuen Plan erst nach einem Neuladen.
- **Zustandsänderungen zwischen den Karten laufen über einen `refreshKey`-Zähler, nicht über einen Ereignisbus.** Ein Bus wäre ein zweiter Weg, den Zustand zu ändern, und beim Lesen wäre nicht sichtbar, wer ihn auslöst.
- **Eine blockierte Aufgabe wird nicht gesperrt dargestellt, sondern mit ihrem Blocker.** Der Start scheitert serverseitig mit 409; die Oberfläche zeigt die Warteschlange, statt den Knopf zu verstecken.
- **Eine tote Sektion `study-tasks-section.tsx` gab es einmal** (ein Monolith mit allem in einer Karte). Sie ist gelöscht. Bei Zweifeln, welches der beiden Aufgabenkarten die richtige ist: es ist `components/manage/study-tasks-card.tsx`, die in `app/lernen` hängt.
- **`useCollection` erlaubt inzwischen read-only.** `actions` ist optional. Das ist nötig, weil `exams-section.tsx` die Planliste zum Generator braucht, dort aber nichts anlegt. Ohne diese Ausnahme müsste jede read-only Sektion drei Attrappen bereitstellen.

### Backend-Fallstricke

- **Eine Serie erzeugt immer nur die *nächste* Occurrence**, und zwar beim `complete`. Eine Serie im Voraus anzulegen hieße, eine Liste zu erzeugen, die man durchklickt statt erledigt. `next_due_on` ist dabei bereits die *kommende* Occurrence, `advance_series()` zählt und rückt nach.
- **`monthly` ist kein Vielfaches von 30 Tagen.** `add_months()` klemmt auf den Monatsende; der 31. Januar plus ein Monat wäre sonst der 3. März. `RECURRENCE_DAYS` enthält `monthly` deshalb **nicht** — wer sie erweitert, umgeht die Monatsrechnung.
- **`occurrence_limit` heißt nicht `limit`.** `limit` ist ein reserviertes SQL-Schlüsselwort; SQLite lehnt die Spalte im `CREATE TABLE` mit `near "limit": syntax error` ab.
- **Der Primärschlüssel von `study_task_dependencies` ist nur das Paar** `(task_id, depends_on_task_id)`, nicht `user_id`. Mit drei PK-Spalten findet `db.get(StudyTaskDependency, (a, b))` nichts, weil der Tuple nicht passt — und das zweite Anlegen einer Kante endet als 409 statt als No-Op.
- **Zyklen werden beim Anlegen geprüft, nicht beim Starten.** `would_create_cycle()` läuft vom neuen Vorgänger zurück zur wartenden Aufgabe. Ohne diese Prüfung wäre die Kette nie erfüllbar, und die Kachel zeigte dauerhaft „Wartet auf …".
- **Abhängigkeiten dürfen nur innerhalb eines Plans.** Über Plangrenzen hinweg wäre eine Reihenfolge sichtbar, die beim Sortieren des einen Plans gilt und im anderen nicht. Deshalb 422, nicht 409.
- **`cancelled` gilt als erledigt für Abhängigkeiten.** Eine stornierte Aufgabe wird nie fertig; ihre Warte-Aufgaben dürfen nicht für immer blockiert bleiben. Deshalb filtert `open_prerequisites_by_task()` auf `status NOT IN ('completed', 'cancelled')`.
- **Beim Löschen einer Aufgabe werden Abhängigkeiten in beide Richtungen entfernt.** Sonst wartet die hintere Aufgabe auf etwas, das es nicht mehr gibt, und es gibt nichts zu lösen.
- **`priority` ist 0–5 und sortiert absteigend.** Höher = wichtiger. Die Obergrenze ist `MAX_PRIORITY = 5` im Schema, die Datenbank prüft aber nur `priority >= 0`. Die Voreinstellung der Liste ist `order=priority`, weil genau das der Grund für die Spalte ist; `due` und `position` gibt es zusätzlich.
- **Der Klausur-Generator verteilt Themen *vorwärts* über das Fenster, nicht rückwärts vom Klausurtag.** Das schwächste Thema (niedrigste `LearningProgress.confidence`, ohne Wert = 0) bekommt den frühesten Termin und die höchste Priorität, weil es am meisten Vorlauf braucht. Die Aufgaben werden in dieser Reihenfolge verkettet. Ein Thema ohne Fortschrittswert gilt als unsicher und kommt nach vorn — ein fehlender Lernstand ist keine gute Nachricht. **Bis zur Fortschritts-Welle war `LearningProgress` ein totes Modell**: es wurde nur vom Demo-Seed geschrieben, also behandelte die Regel „fehlender Wert = 0" *jedes* Thema als unsicher, und die Priorisierung sah deshalb aus, als priorisiere sie nichts. `PUT /api/v1/progress/topics/{topic_id}` schließt die Lücke; ein Test schreibt zwei Themen mit umgekehrten Werten und prüft, dass der erzeugte Plan kippt. `topic_priority()` in `app/services/study.py` liefert dabei nur **0–4** — eine erzeugte Aufgabe erreicht 5 nie, das ist ein Wert, den nur ein Mensch setzt.
- **Ohne `exam.subject_id` oder ohne Themen gibt es 422 mit Klartext**, kein leeres 201. Die Oberfläche zeigt die Meldung an, statt eine leere Liste zu melden.
- **Der Generator ist beim zweiten Klick idempotent**, solange `only_missing` gilt: übersprungen wird ein Thema, für das im selben Plan für dieselbe Klausur und dasselbe Thema schon eine nicht-stornierte Aufgabe hängt.

## Lern-Assistent (`/assistent`, `/api/v1/assistant`)

Ersetzt die Karte „Frag deine Unterlagen“ (Dashboard und `/dokumente` verweisen jetzt hierher). Logik in `app/services/assistant.py`, Router `app/api/assistant.py`, Tests `tests/test_assistant.py`, Oberflaeche `components/assistant/assistant-view.tsx` + `lib/assistant.ts` (bewusst nicht in `api.ts`/`schemas.ts`).

- **Textprotokoll statt Function-Calling.** Das Modell schreibt `AKTION {json}` (Pruefung/Hausaufgabe/Termin) und `MERKE: …`. `tools` im OpenAI-Format koennen Ollama/LM Studio/llama-server nur fuer manche Modelle und ignorieren es sonst stillschweigend. Eine `AKTION`-Zeile ohne `{` bleibt Text.
- **`AKTION` ist nur ein Vorschlag.** Eingetragen wird erst ueber `POST /actions` (Knopf „Eintragen“) – kleine lokale Modelle verrechnen sich bei „naechsten Donnerstag“. Der Prompt bekommt deshalb die naechsten 14 Tage mit Wochentag als Tabelle. Ohne Uhrzeit: 08:00 Ortszeit; Termin ohne Uhrzeit = ganztaegig.
- **„Lernt an dir“ = `assistant_memories` (Migration `0011`)**, kein Feintuning. Saetze stehen bei jeder Anfrage im Prompt, Duplikate (Gross/Klein egal) werden verworfen, alles ist in der Oberflaeche loeschbar.
- **`integration_id` waehlt genau eine KI-Verbindung** und versucht nur diese – sonst kaeme die Antwort womoeglich vom Online-Provider, obwohl ein lokales Modell gewaehlt war. Ohne Angabe gilt `ai_routing` (lokal zuerst).
- **Der Verlauf liegt nur im Browser** (`localStorage`), der Server speichert keine Gespraeche.
- **Zugriff auf alles, nichts erfinden.** Der Prompt enthaelt die Liste aller Dokumente (mit „kein lesbarer Text“), die **Einzelnoten** (`grade_detail_lines`) und ausdruecklich „nichts gefunden“, wenn kein Auszug passt. Nennt die Frage ein Dokument beim Namen („Lebenslauf“, „Mathe Zeugnis“), wird es per `named_documents()` ueber den **Dateinamen** gefunden und vom Anfang an geladen (`EXCERPT_CHAR_BUDGET` 12 000 Zeichen, passend zu 8192 Tokens Kontext) – die Stichwortsuche sucht nur im Text, und ein Lebenslauf enthaelt das Wort selten; vorher erfand Qwen den Inhalt aus den Notizen. Noten gehen mit Freigabe **oder** ohne, wenn alle Wege lokal sind (`grades_shared_online` in `chat()`); die Dokumentenfrage bleibt bei `grade_context_lines` mit Freigabe.
- **Sprache:** Halten zum Sprechen schickt das Transkript direkt ab; „Antworten vorlesen“ liest neue Antworten mit Piper vor (das Einschalten ist die Nutzergeste fuer die Wiedergabe).

## Eingebaute Laufzeiten (`app/services/local_runtimes.py`, nur Desktop)

Der Modellkatalog laedt nur Dateien. Ohne `llama-server`/`whisper-server`/`piper` zeigt ein Modell „Geladen“, und der Assistent sagt trotzdem „kein KI-Modell“ – genau das war der Fehler nach der Assistenten-Welle.

- **`LOCAL_RUNTIMES_ENABLED=true`** (setzt `desktop/lib/runtime.js` und `desktop_main.py`; Default in `Settings` ist `false`, im Docker-Stack bringt der Betreiber seine Laufzeiten selbst mit). Dann holt das Backend die offiziellen GitHub-Releases mit **fester Version + SHA-256** (`RUNTIME_CATALOG`: llama.cpp `b11349`, whisper.cpp `b5130`, Piper `2023.11.14-2`) nach `<MODEL_STORAGE_PATH>/runtimes/`. Downloads nur von `github.com` und dessen Objektspeicher; ein Paket mit falscher Pruefsumme oder einem Pfad aus dem Ziel heraus wird verworfen.
- **Vorladen beim Start, Starten bei Bedarf.** Der Lifespan laedt im Hintergrund die Programme (je 10–25 MB) fuer bereits geladene Modelle; `llama-server`/`whisper-server` starten erst beim ersten Gebrauch auf `127.0.0.1:<freier Port>`. Hoechstens ein Textmodell laeuft, ein Wechsel beendet das alte.
- **Kein verwaister Server.** `shutdown()` im Lifespan; unter Windows haengen die Kindprozesse zusaetzlich an einem Job-Objekt (`KILL_ON_JOB_CLOSE`) – verifiziert: Backend hart beendet ⇒ `llama-server` weg.
- **Eingebauter KI-Weg:** `ai_routing.builtin_route()` (`runtime="builtin"`, `integration_id=0`, dadurch zuerst unter den lokalen Wegen). Gilt fuer Assistent, Begruessung und Dokumentenfrage. Die Wahl im Assistenten (`local_model_id`) wird in `runtimes/selection.json` gemerkt, sonst das groesste geladene Modell.
- **Sprache:** `SpeechService(runtimes=…)` nimmt das eingebaute whisper.cpp/Piper nur, wenn **kein** `WHISPER_SERVER_URL`/`PIPER_BINARY_PATH` eingetragen ist. Mit eingebauten Laufzeiten wird das **groesste** Whisper-Modell genommen (base statt tiny). Fuer macOS gibt es kein fertiges whisper-server.
- **Zwei alte Sprachfehler, die erst gegen echtes ffmpeg auffielen:** `-i pipe:0.webm` ist fuer ffmpeg 8 kein gueltiger Eingang (jetzt `pipe:0`), und ein WAV aus `pipe:1` traegt `0xFFFFFFFF` als Laenge – `repair_streamed_wav()` rechnet sie nach, sonst lehnte die Dauergrenze jede Aufnahme ab („134218 Sekunden“).
- **Echt geprueft** (Windows x64, 02.10.2026): Qwen2.5 3B antwortet ueber den Assistenten in ~14 s inkl. Modellstart, Piper spricht in <1 s, whisper base erkennt WAV und WebM in ~1 s.

### Gespraechsmodus und Robustheit gegen kleine Modelle

- **„Gespräch starten“** (`lib/use-conversation.ts`, Bausteine in `lib/voice.ts`): Mikrofon dauerhaft offen, Satzende per Pegel (`createVoiceDetector`, ~1,1 s Stille), Aufnahme wird im Browser zu **16-kHz-Mono-WAV** (`encodeWav16k`) – das Backend reicht es ohne ffmpeg an whisper.cpp weiter. Waehrend Erkennen/Nachdenken/Vorlesen ist das Mikrofon taub, sonst antwortet der Assistent seiner eigenen Stimme. `ScriptProcessorNode` statt `AudioWorklet`, weil die CSP keine Blob-Module erlaubt.
- **`voice: true`** im Chat verlangt hoechstens drei kurze, vorlesbare Saetze und begrenzt die Tokens. Ein gesprochenes „Ja“/„Nein“ auf offene Vorschlaege (`bestaetigung()`) traegt ein bzw. verwirft – ohne Modellaufruf.
- **Eingebaute Spracherkennung ist fest Deutsch** (`BUILTIN_LANGUAGE`, wenn `WHISPER_LANGUAGE` leer). Mit Auto-Erkennung wurde „Hey, was geht?“ zu „Hey, what's up?“.
- **Schleifenfalle:** `useSpeech().abbrechen()` leert das Transkript **nicht**. Wer auf ein Transkript per Effekt sendet, muss es vorher mit `uebernehmen("")` leeren – sonst feuert der Effekt bei jeder neuen `senden`-Identitaet erneut (passiert: dieselbe Nachricht zwanzigmal).
- **Qwen 3B springt auf Protokoll-Anweisungen an.** Deshalb: `ACTION_PROMPT` nur bei Eintragswunsch (`wants_entry()`), `AKTION` ohne Wunsch wird verworfen; `MERKE` nur in Grossbuchstaben, auch als `MERKE ("…")`, aehnliche Notizen (`SequenceMatcher` ≥ 0,8) werden nicht doppelt gespeichert, „Merke: <bekannte Notiz>“ mitten im Satz wird entfernt, weitergedichtete „Person:“-Zeilen fliegen raus.
- **Datum aus dem Satz schlaegt das Modell** (`date_from_text()`): „übermorgen“, „nächsten Freitag“, „am Dienstag“, „12.11.“ – Qwen machte aus „übermorgen“ an einem Freitag den naechsten Freitag.

### Faultier-Maskottchen

- **Figur** `components/assistant/faultier.tsx` (inline-SVG, CSP-konform), **Zustand** `lib/faultier.ts` (`faultierZustand()`, getestet): `ruhe`, `zuhoeren`, `denken`, `sprechen`, `freut` (2,5 s nach einem Eintrag), `schlaeft` (kein KI-Weg). Gespraechsmodus hat Vorrang vor Senden/Vorlesen.
- **Graustufen** als `--sloth-*` in `globals.css` mit `.dark`-Variante – keine Farbe, passend zum Design. Ganze Figur (`koerper`) in der Seitenleiste von `/assistent` (unter `lg` im leeren Chat) und in der Dashboard-Karte; nur Kopf neben Antworten, beim Nachdenken und im Gespraechsbalken. Nur die juengste Antwort bewegt sich (`still` fuer aeltere).
- Augen folgen dem Zeiger (`folgen`) per direktem DOM-Transform, ohne React-Zustand.

## Desktop-App (`desktop/`)

Electron-Hülle für Verbraucher: kein Docker, kein Server, kein Login. Ablauf und Bauanleitung in `docs/desktop.md`. Was man vor einer Änderung wissen muss:

- **Drei Prozesse, alle nur auf 127.0.0.1:** Electron-Hauptprozess (`main.js`), Next-Standalone-Server per `utilityProcess.fork` (läuft auf Electrons eigenem Node, braucht kein installiertes Node), Backend per `spawn` (PyInstaller-Programm; in der Entwicklung `python backend/desktop_main.py`). Der Frontend-Port ist **fest 47821** (Ausweichen nur, wenn belegt): die Herkunft ist der Schlüssel für Theme, Service Worker und Mikrofon-Freigabe – ein Zufallsport fängt bei jedem Start bei null an.
- **`AUTH_ENABLED=false` + `/api/v1/setup`.** Das Konto entsteht in der Ersteinrichtung (`components/setup-gate.tsx` → `POST /api/v1/setup`), nicht aus `ADMIN_*`. Der Endpunkt antwortet mit **404, sobald `AUTH_ENABLED` an ist** – auf einem Server würde er sonst das erste Admin-Konto an den Schnellsten vergeben. `SetupGate` rendert während der Prüfung nichts statt der Seite, sonst blitzt der 503 „No active user“ auf.
- **`APP_ENV=desktop`, nicht `production`:** `production` verlangt ein `ADMIN_PASSWORD`, das es hier nicht gibt.
- **SQLite bekommt `foreign_keys=ON`, WAL und `busy_timeout`** (`app/db/session.py`, für jede SQLite-URL). Ohne `foreign_keys` verhielte sich die App anders als Postgres (keine `ON DELETE CASCADE`), ohne `busy_timeout` scheitern die Scheduler-Threads an „database is locked“.
- **Das Backend beendet sich bei EOF auf stdin** (`--watch-parent`). Nach einem Absturz der App schickt niemand mehr `kill`; ohne das bliebe ein Server zurück, der Port und Datenbank hält.
- **`secrets.json` im Benutzerordner** hält `SECRET_KEY` und `INTEGRATION_ENCRYPTION_KEY`. Wird nur ergänzt, nie überschrieben – ein neuer Integrationsschlüssel macht alle gespeicherten Zugangsdaten unlesbar (siehe Punkt 9 oben).
- **PyInstaller im eigenen venv** (`desktop/.venv-backend`, nur `requirements.txt`). Mit dem globalen Python sammelte es alles Installierte ein – bis zu scikit-learn. Ergebnis jetzt ~58 MB. Neue Backend-Module, die nur per Namen geladen werden, gehören in `hiddenimports` von `backend/lernraum-backend.spec`; `npm run smoke:backend` fängt ein fehlendes Modul vor dem Nutzer. Die Migrationen liegen im Bundle unter `migrations/`, nicht `alembic/` (Namenskollision mit dem Paket).
- **electron-builder-Fallen:** `extraResources` lässt `.next` ohne expliziten Filter und `node_modules` *immer* weg – deshalb der eigene Eintrag `resources/frontend/node_modules`. Sein Icon-Werkzeug stürzt unter Windows mit „WebAssembly.Memory(): could not allocate memory“ ab – `.ico`/`.icns` erzeugt `scripts/make-icons.py` selbst.
- **npm blockiert Install-Skripte** (`allowScripts` in `desktop/package.json`). Fehlt `node_modules/electron/dist`, hilft `node node_modules/electron/install.js`.
- **Ein Installer je System und Architektur.** PyInstaller kompiliert nicht quer; ein Mac-DMG mit zwei Electron-Architekturen, aber einem Backend, startet auf dem anderen Mac nicht. `desktop.yml` baut Windows x64, macOS arm64 und macOS x64 getrennt. Für Intel-Macs läuft der Job auf `macos-15-intel` – `macos-13` ist abgeschaltet, ein Job darauf bleibt ohne Fehlermeldung ewig „queued“. Die Installer sind unsigniert.
- **`LERNRAUM_USER_DATA`** lenkt den Datenordner um (Tests, portable Nutzung), `LERNRAUM_FRONTEND_URL` nimmt ein laufendes `next dev` statt des gebauten Servers.

## Befehle

PowerShell blockiert `npm.ps1` (Execution Policy) — **`npm.cmd` / `npx.cmd` verwenden**, sonst `PSSecurityException`.

```powershell
# Frontend (cwd = frontend/)
npm.cmd test                       # vitest
npm.cmd run lint                   # eslint
npm.cmd run typecheck              # tsc --noEmit
npx.cmd next build --webpack       # Turbopack panikt, siehe unten

# Backend (cwd = backend/)
python -m pytest
python -m ruff check app alembic tests
python -m mypy app

# Worker (cwd = Repo-Root!, nicht worker/)
python -m unittest discover -s worker/tests
```

Kein Root-`package.json`, kein Task-Runner, keine Pre-Commit-Hooks — jeder Bereich wird einzeln geprüft, immer aus seinem Verzeichnis. `.github/workflows/ci.yml` fährt genau diese Befehle auf jedem PR; siehe „CI und die akzeptierte Baseline".

## ⚠️ `next build` braucht `--webpack`

`npm.cmd run build` (Turbopack) bricht reproduzierbar mit `TurbopackInternalError: ModuleGraph::from_graphs was canceled` ab — **auch bei vollgängigem C:-Laufwerk**. Es ist ein Turbopack-Bug, kein Code- und kein Plattenproblem: `npx.cmd next build --webpack` läuft durch und prerendert alle Routen. Panic-Log: `%TEMP%\next-panic-*.log`.

Für Lint-/Type-Caches im Notfall auf ein anderes Laufwerk legen, falls C: wieder randvoll ist:
`$env:RUFF_CACHE_DIR`, `$env:MYPY_CACHE_DIR`, `pytest --basetemp=<pfad> -p no:cacheprovider`.
`E:` hat ~923 GB frei. `frontend/.next` und `frontend/node_modules` sind reine Git-Ignore-Artefakte.

## Baseline-Status (vor jeder Änderung)

Nicht als Regressionen fehlinterpretieren. Alle Zahlen am 30.09.2026 gegen `HEAD` verifiziert, nicht aus Commit-Nachrichten übernommen:

- `npm.cmd test` → **1 Fehlschlag**, 103 grün (104 Tests, 9 Dateien): `lib/types.test.ts` › „lehnt fehlerhafte API-Daten ab". Der Test prüft, dass `normalizeDashboard()` kaputte API-Daten ablehnt — die Zod-Schemas sind Stubs, also tut es das nicht.
- `npm.cmd run lint` → **22 Fehler**, ausschließlich `@typescript-eslint/no-explicit-any`, verteilt auf **7 in `lib/schemas.ts` + 15 in `lib/types.ts`**. Absichtliche Arbeitsumgehung, kommentiert in `lib/types.ts:8`: die Zod-Schemas sind `z.object({}).passthrough()`-Stubs, die nichts validieren. Die Aufgaben- und Fortschrittsschemas sind inzwischen echte Schemas, deshalb bleibt die Zahl trotz mehr Code gleich — die 22 sind alle in den Stub-Dateien. `typecheck` ist dagegen sauber.
- `python -m pytest` (aus `backend/`) → **188 grün**, `ruff` sauber, `mypy` sauber über 63 Dateien.
- Worker-Tests: 4 grün, aus dem **Repo-Root**.

Die beiden roten Frontend-Befehle sind inzwischen **kein herrenloser Ausnahmezustand mehr**: `.github/ci/known-failures.mjs` benennt genau diese eine Test-ID und die zwei (Datei, Regel)-Paare mit einer Obergrenze je Datei. Wer eine Zahl hier oben verändert, muss sie dort mitverändern, sonst fällt der Job.

## Proxy-/API-Pfad-Falle

Der Browser ruft **ausschließlich** `/api/backend/v1/...` auf. `isValidProxyPath` (`lib/proxy.ts:90`) verlangt, dass das **erste Segment exakt `v1` ist** — `/api/backend/health` oder `/api/backend/api/v1/...` ergeben 400. Rewrite-Ziel:

```text
/api/backend/v1/<rest>  ->  ${BACKEND_INTERNAL_URL}/api/v1/<rest>
```

- `BACKEND_INTERNAL_URL` ist **serverseitig** und darf **nie** ein `NEXT_PUBLIC_`-Präfix bekommen. Compose setzt `http://backend:8000`; `lib/proxy.ts:38` fällt nativ auf `http://localhost:8000` zurück.
- Für natives `npm run dev` muss das Backend separat auf Port 8000 laufen, sonst `$env:BACKEND_INTERNAL_URL = "http://localhost:8000"` setzen.
- Alle `/api/v1`-Routen außer Health brauchen eine Session und isolieren Daten pro Benutzer.

## Frontend-UI-Konventionen

- **Tailwind v4, CSS-first: es gibt keine `tailwind.config.js`.** Alle Design-Tokens liegen in `app/globals.css` — die `:root`/`.dark`-CSS-Variablen und der `@theme inline`-Block. Ein Token hinzufügen heißt: `globals.css` editieren, keine JS-Konfiguration.
- Dark Mode über `next-themes` mit `attribute="class"`; die Klasse ist `.dark` (siehe `@custom-variant dark` in `globals.css:3`), **nicht** `[data-theme]`.
- **Theme-Farben an vier Stellen hartkodiert**: `globals.css`, `app/layout.tsx` (`viewport.themeColor`), `app/providers.tsx` (`#ffffff` / `#0a0a0a`) und `desktop/main.js` (`BACKGROUND`, Fenstergrund vor dem ersten Rendern). Palette ändern ⇒ alle synchronisieren, sonst blitzt beim Start oder im Meta-Tag die alte Farbe.
- **Design ist schwarz-weiß mit kleinen Rundungen** (seit der Desktop-Welle). Graustufen-Tokens in `globals.css`, `--radius-md` = 6px; `rounded-xl`/`2xl` sind bewusst auf 8/10px gedeckelt, damit ältere Stellen nicht als Pillen auffallen. Es gibt keine Farbklassen, Gradienten oder Schatten; Struktur tragen Haarlinien. Zustände werden über Gewicht, Füllung und deutsche Labels unterschieden.
- **Typografie-Regeln, die den „generierten“ Look verhindern:** Schrift ist Geist (npm-Paket `geist`, von Next selbst ausgeliefert – CSP-konform und offline). Nur `font-medium`/`font-semibold`, kein `font-bold`/`extrabold`. **Keine** `uppercase tracking-[…]`-Überschriften, keine `Sparkles`-Icons, keine Motivationssätze („Du machst den Unterschied“) in Karten. Kartentitel sind `<CardTitle>` ohne Icon; Seitentitel `text-[22px] font-semibold`. Fließtext 13px.
- **`desktop:`- und `mac:`-Varianten** (`globals.css`) greifen nur in der Desktop-App (`data-desktop`/`data-platform` am `<html>`, gesetzt vom Preload). `.app-drag`/`.app-no-drag` markieren Flächen, an denen man das Fenster zieht.
- **Strikte CSP in `next.config.ts:4-17`**: `script-src 'self' 'unsafe-inline'` (+ `'unsafe-eval'` nur in Dev), `style-src 'self' 'unsafe-inline'`, `img-src 'self' data: blob:`, `font-src 'self' data:`, `connect-src 'self'` in Produktion. Also: keine Google-Fonts-Link, keine CDN-Bilder/Skripte, keine externen Fonts zur Laufzeit.
- `viewportFit: "cover"`; die mobile Bottom-Nav braucht `env(safe-area-inset-bottom)`.
- **Vitest läuft nur mit `include: ["lib/**/*.test.ts"]` und `environment: "node"`** (`vitest.config.mts`). Komponenten-/DOM-Tests werden **stillschweigend nicht ausgeführt** — nicht erwarten, dass sie greifen.
- Service Worker `public/sw.js` ist network-first mit Fallback auf `/offline/`, `CACHE_NAME = "lernraum-static-v9"`. Bei Änderung der App-Shell den Cache-Namen bumpen, sonst sehen Nutzer eine veraltete Shell.
- Navigation: Anker des Dashboards (`/#overview`, `/#schedule`, `/#homework`, `/#exams`, `/#calendar`) in der Gruppe „Heute“, eigene Routen (`/lernen`, `/faecher`, `/dokumente`, `/fortschritt`) in „Bibliothek“, `/ki` („KI & Modelle“: KI-Dienst, lokale Modelle, Stimmen) in „Werkzeuge“, `/settings` unten. `#usage` gibt es nicht mehr – die Zahlen stehen in der Kennzahlenleiste. Aktivzustand über `isNavigationHrefActive` in `lib/navigation.ts` (hash- und pathname-abhängig, `usePathname` + `hashchange`). **Keine Hash-Anker auf Unterseiten**: die Shell liest den Hash für den Aktivzustand, ein `#abschnitt` auf `/settings` oder `/ki` ließe den Navigationspunkt erlöschen. Die Einstellungen merken ihren Reiter deshalb in `?tab=`, die KI-Seite springt per `scrollIntoView`.
- **KI-Verbindungen (`ai_provider`) stehen nur auf `/ki`.** Die Einstellungen filtern die Art aus Liste *und* Katalog; beide Seiten nutzen dieselbe exportierte `IntegrationsCard` aus `settings-view.tsx`.
- `@/*` → Wurzel von `frontend/`. `cn()` = `clsx` + `tailwind-merge`.
- **UI-Texte sind deutsch** (`<html lang="de">`), auch Fehlermeldungen im Proxy-Route-Handler und die Testnamen.
- Das eigentliche Dashboard ist `components/dashboard/dashboard-view.tsx` (~670 Zeilen), eingehängt in `app/page.tsx`; Shell/Nav in `components/app-shell.tsx`. Die nächste Stunde steht als erste Zelle der Kennzahlenleiste (`TodayStrip`, Anker `#focus`), nicht als eigene Karte. `ProgressCard`, `GradesCard` und `RewardsCard` stehen auf `/fortschritt`, nicht im Dashboard. Der Stundenplan steht oben in der Hauptspalte und startet mit „Heute“; seine Zeilen sind die **Uhrzeit in Ortszeit** (`"07:45"`), nicht der volle Zeitstempel — sonst bekommt jeder Tag eigene Zeilen und die Wochenspalten stehen versetzt untereinander.

## Backend-Konventionen

- ruff: `line-length = 120`, `target-version = "py312"`, Regeln `E4,E7,E9,F,I` — **`I` erzwingt Importsortierung**, die häufigste Lint-Ursache.
- mypy: `warn_return_any`, `no_implicit_optional`, `check_untyped_defs` — ein `-> None` fehlt nicht zufällig.
- pytest: `asyncio_mode = "auto"` (async-Tests ohne Marker), `testpaths = ["tests"]`. Es gibt nur `tests/conftest.py` + `tests/test_backend.py` (160 Testfunktionen, 188 Tests nach Parametrisierung).
- **Backend-Tests brauchen kein Postgres**: `conftest.py` legt pro Test eine SQLite-DB in `tmp_path` an, fährt `alembic upgrade head` und überschreibt `get_db` per `dependency_overrides`.
- **FastAPI-Dependencies als `Annotated`-Alias, nie zusätzlich mit `= Depends()`**: `deps.py` definiert `CurrentUser`/`DatabaseSession`/`AppSettings` bereits als `Annotated[X, Depends(...)]`. Ein zusätzliches `= Depends()`-Default lässt FastAPI mit `AssertionError: Cannot specify Depends in Annotated and default value together` abstürzen — **beim Import von `app.main`, also startet der ganze Container nicht**. Parameter mit `Annotated`-Alias daher immer ohne Default deklarieren; ein Parameter mit Default (z. B. `file: UploadFile = File(...)`) muss **hinten** stehen.
- **Neue Router müssen in `create_app()` registriert werden** (`app/main.py`), sonst sind alle Routen 404 — der Import in der Funktion allein registriert nichts. Das hat den kompletten `/api/v1/integrations`-Block initially unerreichbar gemacht.
- Migrationen immer als neue nummerierte Revision in `backend/alembic/versions/`; **`backend/entrypoint.sh`** führt `alembic -c alembic.ini upgrade head` aus, Uvicorn startet erst bei Erfolg. Es gibt keine zweite Definition mehr — siehe „Image-Bau und Build-Kontexte".
- `Settings` (`app/core/config.py`) ist `case_sensitive=False`, `extra="ignore"`, liest `.env`, und nutzt `AliasChoices`, um beide Stile zu akzeptieren (`APP_ENV` **und** `environment`). Secrets sind `SecretStr`. Bei `APP_ENV=production` werden Platzhalterwerte aus `_PLACEHOLDER_SECRETS` abgelehnt.
- `get_settings()` ist `@lru_cache` — Settings werden prozessweit gecacht.

## Integrationen (`/api/v1/integrations`)

- Katalog, Specs und Feld-Listen stehen in `app/services/integrations.py` (`INTEGRATION_SPECS`, drei Kinds: `webuntis`, `ai_provider`, `documents`). `integration_spec(kind)` wirft `IntegrationValidationError` bei unbekanntem Kind.
- Zugangsdaten werden **nie im Klartext** gespeichert: `app/core/crypto.py` leitet per HKDF-SHA256 einen AES-GCM-Schlüssel aus `INTEGRATION_ENCRYPTION_KEY` ab, Payload-Präfix ist `sa1.`. `encrypt_credentials`/`decrypt_credentials`, Fehler als `CredentialDecryptionError`.
- **Jeder** Lese-/Schreibpfad braucht den Key über `require_integration_encryption_key(settings)`; fehlt er, antworten die Endpoints mit **503** (nicht 500). `GET /catalog` braucht keinen Key und **keine** Session.
- Der `test`-Endpoint führt einen **echten** Verbindungs-Test aus. Reihenfolge: `missing_fields` → `enabled` → `check_integration_connection()`. Ergebnis ist `connected` (Erfolg), `error` (Fehlertext in `last_error`) oder `disabled`. Alle drei Kinds haben einen Adapter; der `raise`-Zweig am Ende von `check_integration_connection()` ist nur noch die Absicherung für unbekannte Kinds.
- WebUntis-Adapter: `app/services/webuntis.py`. JSON-RPC 2.0 per POST auf `{base_url}/jsonrpc.do?school={school}` (Methode `authenticate`, dann `logout`). `build_rpc_url` normalisiert auf `/WebUntis` und quotet den Schulkürzel. Fehlercodes: `-8520/-8521/-8527` = Zugangsdaten, `-8500` = unbekanntes Kürzel, `-8509/-8510` = Sitzung, `-8522` = Timeout.
- KI-Provider-Adapter: `app/services/ai_provider.py`. Testet bewusst über `GET {base_url}/models` — der billigste Read-only-Check für Erreichbarkeit, Schlüssel und Modellverfügbarkeit, ohne Generierung und ohne Tokenverbrauch. `build_models_url` ergänzt eine fehlende `/v1`-Stufe, weil das der häufigste Konfigurationsfehler ist. `model` darf `"Auto"` sein, dann wird das erste Modell der Liste gewählt; sonst wird der Name tolerant verglichen (`llama3.2` matcht `llama3.2:latest`). Unbekannte Modelle nennen bis zu vier Vorschläge aus der echten Liste. 401/403 → `AiProviderAuthError`, 404 → Hinweis auf `/v1`.
- **`localhost` in einer Integrations-URL ist der Container, nicht der Host.** Das Backend läuft in Docker; ein auf dem Host laufendes Ollama/LM-Studio ist unter `localhost:PORT` **nicht** erreichbar (ConnectError), sondern nur über `host.docker.internal:PORT`. Verifiziert: `localhost:3001` → `[Errno 111] Connection refused`, `host.docker.internal:3001` → 401 vom echten Server. Das trifft beide Adapter, weil die URL in der DB gespeichert und unverändert verwendet wird.
- **WebUntis `su[].name` ist das Kürzel, `su[].longname` der ausgeschriebene Name.** Live an allen 21 Fächern geprüft: `name="F"` / `longname="Französisch"`. Der Importer speichert deshalb `longname` nach `Subject.name` und `name` nach `Subject.short_name`; die Oberfläche zeigt das Kürzel. `externalkey` wird angefordert (`_ENTITY_FIELDS`), liefert aber **0 Treffer** — das Portal kennt das Feld nicht.
- **Ausfälle stehen in `code`, nicht in einem Feld `cancelled`.** `parse_timetable` sieht eine **Liste** von Einträgen (kein Objekt mit `timeGrid`). `CANCELLED_CODE = "cancelled"` wird nach `Lesson.is_cancelled` übersetzt; ein unbekannter `code` wie `roomChanged` bleibt eine normale Stunde. Die Einheit selbst bleibt vollständig in der Antwort.
- **`generate()` kostet Token, `check_connection()` nicht.** `AiProviderClient.generate()` ruft `POST {base_url}/chat/completions` mit Timeout 30 s (der Modelllauf auf einer lokalen CPU braucht länger als `GET /models`). `model="Auto"` löst über `_resolve_model()` auf und holt dafür die Modellliste — ein Test mit `Auto` braucht also zwei gemockte Antworten. `build_chat_url` kürzt ein `/models`-Suffix, sonst entstünde `/models/chat/completions`.
- **Ein Spaltenname darf nicht `text` heißen, wenn `text` der importierte SQLAlchemy-Typ ist.** `GreetingCache.sentence` statt `.text`; `.text` im Klassenrumpf würde den Typ verdecken. mypy meldet das als `"Mapped[str]" not callable`.
- **Fehlerbody vor dem HTTP-Status auswerten**: WebUntis antwortet auf ein falsches `school`-Kürzel mit HTTP **404** und JSON-RPC-`-8500` im Body. Wer zuerst auf den Status prüft, verliert die aussagekräftige Meldung. `_call` parst deshalb `_parse_body()` zuerst.
- **`react-hooks/set-state-in-effect` ist eine Error-Regel in `eslint-config-next`** und akzeptiert die Updater-Form von `setState`, aber nicht die Objekt-Form. Der Unterschied ist nicht logisch, aber real: `use-dashboard.ts` bleibt sauber, dieselbe Logik als `setState({...})` nicht. Im Zweifel `window.setTimeout(() => setState(...), 0)` — so macht es `use-now.ts`.
- **Ein Test auf einer geteilten `Session` liest den Identity-Map-Cache, nicht die DB.** Nach einem HTTP-Aufruf mit eigener Session (`db.expire_all()`) den Wert erneut lesen, sonst prüft der Test den alten Stand. Hat mich beim `short_name`-Backfilltest in die Irre geführt.
- `school` ist das **Kürzel** aus der Browser-Adresse, nicht der Anzeigename. Bei `-8500` nennt die Fehlermeldung per `suggest_school_slug()` den normalisierten Vorschlag. Base-URL und Kürzel stimmen für Lessing-Gymnasium Karlsruhe z. B. beide auf `lessinggymnasiumkarlsruhe`.
- `credentials: {}` im PATCH leert die gespeicherten Zugangsdaten und setzt `status` auf `configured`.

## Stundenplan-Import (`POST /api/v1/integrations/{id}/sync`)

- `app/services/integrations.py`: `sync_integration()` ist der Dispatcher, `_sync_webuntis()` delegiert an `timetable_import.import_timetable()`. `sync_window()` liefert Montag der aktuellen Woche bis `DEFAULT_SYNC_WEEKS = 4` Wochen später. Nur `webuntis` ist implementiert; andere Kinds werfen `IntegrationValidationError` mit deutscher Meldung.
- Ein logisch fehlgeschlagener Sync antwortet mit **HTTP 200** und `ok: false` (wie `/test`). Nur Session-, Ownership- und Key-Fehler ergeben 4xx/503. Reihenfolge: `missing_fields` → `enabled` → Key → `sync_integration()` → `import_timetable()`. **Es gibt keine `check_integration()`-Vorschaltprüfung**, und das ist Absicht: der Abruf authentifiziert sich beim Schulportal von selbst, ein zweiter Roundtrip davor würde nur den Knopf langsamer machen, ohne etwas zu prüfen, was nicht schon `missing_fields` und `enabled` prüfen. (Der `/test`-Endpunkt macht den echten Verbindungstest — dort ist er richtig und notwendig.)
- **`getTimandis`… die Methode heißt `getTimetable`** und wird über `{base_url}/WebUntis/jsonrpc.do?school={school}` aufgerufen. Der `params`-Wert ist ein **Objekt**, kein positionelles Array — im minifizierten `main.js` sieht man die generische Array-Dekodierung, aber der interne Aufruf benutzt `SchoolUtils/WebUntis/src/base.ts` als Objekt. Das ist live gegen Lessing-Gymnasium verifiziert, nicht nur aus dem Bundle abgeleitet: mit Array liefert das Portal einen Fehler.
- Pflichtfelder in `options`: `element={id: personId, type: personType}` (Schüler `5`, Klasse `1`, Lehrer `2`, Fach `3`, Raum `4`), `startDate`/`endDate` als **`YYYYMMDD` ohne Trennzeichen**, `id` als Unixzeit in ms, dazu die `show*`-Flags und `classFields`/`roomFields`/`subjectFields`/`teacherFields` = `["id","name","longname","externalkey"]`.
- Antwortobjekt: `id`, `klasse`/`teachers`/`rooms`/`subjects`, `timeGrid`. Eine Einheit: `id`, `date`, `startTime`/`endTime` (`HHMM`), Relationsarrays `kl`/`te`/`su`/`ro`, `lsnumber`, `activityType`, optional `lstext`/`code`. Fehlende Pflichtfelder ⇒ Eintrag wird übersprungen und in `skipped_entries` gezählt, der Import bricht **nicht** ab.
- **Zeitannahme:** WebUntis liefert Schul-Ortszeit. `timetable_import` interpretiert `startTime`/`endTime` in `current_user.timezone` und schreibt über `as_utc()` UTC. `07:45 Europe/Berlin` ⇒ `05:45+00:00`. Verifiziert: `09:35` ⇒ `07:35+00:00`.
- **`db.flush()` nach den Stammdaten-Upserts ist Pflicht, nicht Kosmetik.** Ohne Flush haben neue `Subject`/`Teacher`/`Room`-Zeilen kein `id`, und die Lesson-Verweise bleiben `None` — der Import lief mit 60 Lessons, aber alle ohne Fach, Lehrer und Raum. Genau dieser Fehler ist passiert.
- Stammdaten werden anhand `source == "webuntis"` + `external_id` wiederverwendet, sonst über den **Namen** — sonst erzeugt der Import Dubletten, wenn man ein Fach manuell angelegt hat. Fach/Lehrer nehmen `longname`, Räume `name` (`longname` ist dort teils der Klassenname).
- Der Replace löscht nur Lessons mit derselben `sync_source_id` im Fenster. Manuelle Lessons und Lessons außerhalb des Fensters bleiben unangetastet; das ist mit einem Test abgesichert.
- `code` und `lsnumber` werden nicht gespeichert, weil `Lesson` keine Felder dafür hat. `activityType` wird geparst, aber nicht persistiert; der Titel kommt aus `lstext`.
- **Der Sync läuft jetzt auch planmäßig** — als Thread im Backend-Lifespan, nicht im Worker. Auslöser bleibt zusätzlich der Button **Stundenplan aktualisieren** in den Einstellungen (`syncIntegration()` in `frontend/lib/api.ts`), und der Knopf holt **immer**, auch wenn der Tageslauf schon gelaufen ist. Einzelheiten im Abschnitt „Planmäßiger WebUntis-Abruf".
- `IntegrationSyncResult` hat zusätzlich zu den Test-Feldern `imported_lessons`, `subjects`, `teachers`, `rooms`, `skipped_entries`, `window_start`, `window_end`, `last_sync_at`. Der Frontend-Zod-Schema `integrationSyncResultSchema` in `lib/schemas.ts` muss diese Felder spiegeln, sonst `.parse()` strippt sie stillschweigend.
- **`_resolve` aktualisiert bestehende Stammdaten nicht.** Ein gleichnamiger Treffer wird wiederverwendet, aber `source`/`external_id`/`name` bleiben unverändert. Deshalb trägt der Importer `Subject.short_name` **nachträglich** nach (`if not found.short_name`), sonst bliebe ein vor dem Feld importiertes Fach dauerhaft ohne Kürzel. Von Hand gepflegte Kürzel werden dabei nicht überschrieben.

## Planmäßiger WebUntis-Abruf (`app/services/integration_scheduler.py`)

- **Der Scheduler läuft im Backend, nicht im Worker.** Der Abruf braucht den WebUntis-Adapter, das Entschlüsseln und den Import in die DB — allesamt Code hinter `backend/app`, den der Worker-Kontext bewusst nicht sieht. Ihm eine zweite Ausprägung desselben Ablaufs zu geben, hieße ihn an zwei Stellen pflegen. Der Worker blieb deshalb Healthcheck-only.
- **Zweistufig gebaut, damit die Tests keinen Zeitgeber brauchen**: `run_scheduled_integration_syncs()` ist eine reine Funktion über einer Session und entscheidet je Integration, ob der Abruf fällig ist; `IntegrationSyncScheduler` ist der dünne Thread, der sie aufruft und weder Tabelle noch Zeitfenster kennt. Der Thread wird über `job` und `session_factory` gereicht — beides im Test eine Attrappe.
- **Die Idempotenz sitzt ausschließlich im Scheduler.** `scheduled_sync_is_due()` prüft `last_sync_at` gegen `WEBUNTIS_SYNC_INTERVAL_SECONDS`; `run_integration_sync()` prüft es **nie**, und zwar absichtlich: sonst würde der manuelle Knopf ins Leere laufen, wenn der Tageslauf schon war. Die zweite Hälfte der Idempotenz ist der **nicht blockierende `threading.Lock`** in `try_run_once()` — ein `None` dort heißt „ein anderer Lauf hat den Lock", kein Fehler. `scheduled_sync_is_due()` rechnet dabei über `as_utc()`, weil SQLite `last_sync_at` ohne Zeitzone liefert; ohne das wäre die Differenz mit `now` ein `TypeError` und der planmäßige Abruf stünde still.
- **`run_integration_sync()` wirft nur noch `RuntimeError` für einen fehlenden `INTEGRATION_ENCRYPTION_KEY`** (Konfigurations-, kein Datenproblem) und reicht `SQLAlchemyError` durch. Alles andere landet als `IntegrationSyncRun(ok=False)` in `last_error`, damit weder Endpunkt noch Scheduler eine Ausnahme behandeln müssen. Der Endpunkt übersetzt den einen `RuntimeError` in **503**.
- **`record_integration_sync_error()` rollt zuerst zurück und liest die Zeile danach neu.** Ein fehlgeschlagener Import hinterlässt sonst eine halb geschriebene Sitzung; und die Zeile aus dem Identity-Map-Cache zurückzuschreiben würde den alten Zustand konservieren.
- **Nutzerisolation ist die wichtigste Eigenschaft der Schleife**: ein Fehler bei Benutzer A wird in `last_error` geschrieben und der Lauf macht mit B weiter. Ein `SQLAlchemyError` schreibt dagegen *nichts* — der Import hat dann ohnehin nichts verändert. Die Reihenfolge `ORDER BY SyncSource.user_id, SyncSource.id` ist Absicht: ein Fehler trifft damit immer denselben Benutzer, und Tests sehen eine vorhersehbare Abfolge.
- **Nur `webuntis` ist planmäßig** (`SCHEDULED_KIND`). Der Dokumentabruf bleibt beim Knopf: er betrifft ein lokales Verzeichnis und kennt kein Zeitfenster.
- Der erste Lauf wartet `STARTUP_DELAY_SECONDS = 30`, danach je `WEBUNTIS_SYNC_INTERVAL_SECONDS`. **Die Verzögerung wird erst nach dem ersten Lauf auf das volle Intervall gesetzt** — konstant 30 s liefe der Abruf im Sekundentakt. Abschaltbar per `stop_event`; `_safe_run_once()` fängt alles, weil ein Thread, der an einem Lauf stirbt, den planmäßigen Abruf für immer beenden würde.
- `application.state.integration_scheduler` existiert **immer**, auch als `None`, wenn der Scheduler aus ist — Tests und ein Status-Endpunkt müssen nicht erst prüfen, ob die Einstellung da ist.
- **`WEBUNTIS_SYNC_ENABLED` ist in `Settings` per Default `false`, in Compose `:-true`.** Der Unterschied ist Absicht: ein `natives python -m uvicorn` soll beim Start keine ungefragten Netzverbindungen zu einem Schulportal aufbauen, der Compose-Stack soll es. `WEBUNTIS_SYNC_INTERVAL_SECONDS` ist `86400`, validiert mit `gt=0, le=604800`.

## Willkommenssatz (`GET /api/v1/greeting`)

- `app/services/greeting.py` baut den Satz aus `DashboardResponse`, nicht aus eigenen Queries. `_next_lesson()` rechnet **in der Nutzerzeitzone**: die Lektionen liegen als UTC vor, und `07:45 Europe/Berlin` darf nicht als `06:45` im Satz stehen. `zone: ZoneInfo` ist deshalb ein Pflichtparameter von `build_fallback_greeting` und `build_prompt` — sonst rutscht man unbemerkt in UTC.
- **Ohne Provider kommt `source: "fallback"`, nie ein Fehler.** Kein Provider, kein Schlüssel, ConnectError, 401, leere Antwort: alles führt zum deterministischen Satz. Die Oberfläche zeigt die Herkunft als Badge an, damit niemand einen Zufallssatz für eine KI-Aussage hält.
- **Ein Modellaufruf pro Tag, nicht pro Seitenaufruf.** `greeting_cache` hält je Nutzer und `generated_on` genau eine Zeile (`uq_greeting_cache_user_day`). `POST /api/v1/greeting/refresh` überschreibt sie. Ohne Cache kostete jeder Reload Token und 1–5 s Wartezeit.
- `AiProviderError` wird **gefangen** und in `logger.warning` geschrieben, nicht weiter gereicht. Eine kaputte KI-Konfiguration darf das Dashboard nicht lahmlegen.
- Der Client hat **keinen Demo-Fallback**, anders als `getDashboard`. Ein erfundener Begrüßungssatz wäre nicht als solcher erkennbar.

## Lernfortschritt und Lernprotokoll (`/api/v1/progress`)

Vier Routen in `app/api/progress.py`, Logik in `app/services/progress.py`: `PUT /topics/{topic_id}` (Upsert), `GET /topics`, `GET /insights`, `PATCH /goal`. **Keine Migration** — `LearningProgress` hatte `confidence` und `last_reviewed_at` schon, und das Wochenziel liegt in der vorhandenen JSON-Spalte `settings.preferences` unter `weekly_goal_minutes` (Default 300, Merge statt Overwrite, damit kein fremder Schlüssel verloren geht).

- **`LearningProgress` war bis hierher ein totes Modell.** Nur der Demo-Seed schrieb die Zeile. Der Klausur-Generator las sie schon und rechnete „fehlender Wert = 0" — deshalb behandelte er jedes Thema als unsicher und die Priorisierung sah aus, als priorisiere sie nichts. Das ist die Kette, die man kennen muss, wenn sich der Generator „plötzlich" gleichgültig verhält.
- **Warum `PUT` und nicht `PATCH`:** der Aufruf legt die Zeile bei Bedarf an und ändert sie sonst. Er ist damit der erste Ort, an dem eine Lernende ihren Lernstand überhaupt schreiben kann.
- **`last_reviewed_at` hat drei Zustände**, und alle drei sind Absicht: **nicht gesetzt** heißt *jetzt* (eine Einschätzung ohne Datum ist eine Wiederholung in diesem Moment, und ohne Anker gäbe es kein Intervall), **explizit `null`** löscht den Anker, ein **Zeitpunkt** wird übernommen. `notes` bleibt unberührt, wenn das Feld fehlt. Ein `as_utc()` auf einen naiven Wert ist hier Pflicht — dasselbe wie in `app/api/study.py`.
- **Ohne `last_reviewed_at` gibt es keinen Wiederholungstermin — nicht „heute".** Eine Behauptung ohne Beleg; ein noch nie wiederholtes Thema bekommt deshalb `next_review_on = null` und `interval_days = null`, während die Bezeichnung „unsicher" trotzdem stehen bleibt. Der fehlende Lernstand ist eine Aussage über das Thema, nur keine über einen Termin.
- **Fünf Stufen, je ein Intervall** (`CONFIDENCE_STEPS`): unsicher 1 Tag, geübt 3, sicher 7, fest 14, gefestigt 30. Das ist **kein SRS** — keine Kurve, kein Faktor, keine Zufallsjährung. Die Regel soll in einem Satz erklärbar sein, und das Frontend bekommt Wert, Label und Intervall vom Backend geliefert (`confidence_steps` in jeder Antwort), damit nicht zwei Tabellen über dieselbe Frage existieren.
- **`confidence_step` ist ein Extrafeld neben `confidence`, nicht ein Alias.** Ein API-Aufrufer darf `0.9` setzen; im `<select>` gäbe es dafür keinen passenden Option-Value, und die Anzeige hinge an der Anzeige statt am Intervall. Das `<select>` bindet darum an `confidence_step` (`progress-card.tsx`).
- **`populate_existing=True` in `one_topic_progress()` ist Pflicht.** Beim Schreiben ist das Thema derselben Sitzung schon geladen, und ein bereits geladenes Attribut wird sonst nicht erneuert — der Aufruf wäre still erfolgreich und die Oberfläche zeigte den alten Lernstand. Deshalb läuft auch die Antwort nach dem Schreiben durch dieselbe Funktion wie beim Lesen, sonst zeigte die Kachel nach dem Speichern einen anderen Termin als die nächste Ladespalte.
- **Ein fremdes Thema gibt 404, nicht 403.** 403 würde verraten, dass die Id überhaupt existiert. Die Nutzertrennung ist hier Pflicht, weil ohne sie jeder den Fortschritt jeder fremden Id schreiben könnte.
- **Ein Fenster, eine Abfrage, eine Umrechnung.** Alles Zeitbezogene in `learning_insights()` kommt aus *einer* Query über `STREAK_WINDOW_DAYS = 365`. Jede Zeile wird genau einmal per `local_day()` in die Nutzerzone umgerechnet, danach wird nur noch mit `date` gerechnet. Sonst könnte dieselbe Session am Monatswechsel in zwei Tagen landen — oder, schlimmer, in zwei Summen. **Der Test dafür ist nicht kosmetisch:** Montag 00:10 Berlin ist Sonntag 22:10 UTC, und der Bucket muss Montag sein.
- **Eine Session zählt an ihrem *Starttag*.** Wann sie endet, ist für die Statistik nebensache.
- **Offene Sessions zählen nirgends mit**, sondern separat als `open_sessions` und `active_minutes`. `_credited_minutes()` gibt einer offenen Session **null** und nicht ihre bisherige Laufzeit — sonst stünde jede laufende Session als Zwischenstand *und* nach dem Beenden noch einmal mit echter Dauer in der Statistik. Die Karte weist deshalb ausdrücklich darauf hin, dass laufende Zeit erst nach dem Beenden zählt.
- **Serie:** ein Lerntag ist ein Kalendertag in der Nutzerzone mit **mindestens `MIN_DAY_MINUTES = 1` Minute abgeschlossener Sessions** — ein Start und sofortiger Stopp erzeugt also keine Serie. **Heute bleibt offen**: wer morgens nachsieht und gestern lernte, verliert die Serie erst, wenn gestern *und* heute leer sind; sonst fiele die Zahl jeden Morgen um eins. `longest_days` rechnet über dasselbe 365-Tage-Fenster.
- **Wochenziel in Minuten, nicht in Sessions** — fünf Minuten und neunzig Minuten sind nicht dasselbe. Die Woche läuft **Montag–Sonntag in der Nutzerzone** (`week_bounds()`) und wird **nicht** anteilig auf die vergangenen Tage gekürzt: ein Rückstand am Mittwoch ist eine Information und kein Rechenfehler.
- **`WEEKDAY_WINDOW_DAYS = 84` für die Wochentagsverteilung, nicht 365** — Gewohnheiten verschieben sich, und ein Jahr beantwortet eine andere Frage. Alle sieben Tage werden geliefert, auch die leeren: eine Lücke ist eine Aussage.
- **Sessions ohne Thema werden nicht verworfen.** Der Weg zum Fach geht über `StudyPlanTask.topic_id → Topic.subject_id`; eine Aufgabe ohne Thema landet unter `Ohne Fach`, und `share_percent` bezieht sich auf die Summe **inklusive** dieses Korbs — sonst wären es nicht die Anteile an der Lernzeit, die man in der Summe sieht.
- **Die Karte steht auf `/fortschritt`, getrennt von den Start/Stopp-Knöpfen im Dashboard.** Früher hing sie dort am `refreshKey`-Zähler, den `StudyTasksCard` nach `start`/`stop`/`complete` hochzählte. Auf der eigenen Seite lädt sie beim Öffnen frisch; `refreshKey` bleibt als optionale Prop, falls Karte und Knöpfe je wieder zusammenkommen.
- **`useProgress()` lädt nach dem Speichern nicht neu.** Eine Konfidenz ändert genau eine Zeile, das Wochenziel genau ein Feld in `insights.week` (kommt als Antwort zurück). Stattdessen wird gezielt ersetzt — das erhält die Sortierung des Backends, und die Themenliste springt beim Bewerten nicht.

## Verifizierte Config-Lücken (nicht in README dokumentiert)

Geschlossen:

1. ~~`INTEGRATION_ENCRYPTION_KEY` fehlt in Compose~~ → jetzt in `docker-compose.yml` (Pflicht, `${INTEGRATION_ENCRYPTION_KEY:?...}`), `.env.example` und README-Tabelle.
2. ~~`cryptography` fehlt in `pyproject.toml`~~ → jetzt in `[project] dependencies` gepinnt.
3. ~~`INTEGRATION_TIMEOUT_SECONDS` nicht in Compose~~ → jetzt mit Default `10` weitergereicht.
4. ~~`httpx` fehlt als Runtime-Dependency~~ → steht in `[project] dependencies` und `requirements.txt`.
5. ~~**README nennt 2 Alembic-Revisionen, es gibt 5**~~ → das ToDo war selbst veraltet: das README listete bereits alle **6** (`0001` Basisschema inkl. pgvector, `0002` offene Lernsessions, `0003` Integration-Credentials, `0004` `subjects.short_name` + `lessons.is_cancelled`, `0005` `greeting_cache`, `0006` Task-Manager).
6. ~~**Migrations-Start doppelt definiert**~~ → gelöst. `backend/entrypoint.sh` ist die einzige Quelle, Compose überschreibt nichts mehr und bindet nichts mehr ein. Siehe „Image-Bau und Build-Kontexte".
7. ~~**Root-`.dockerignore` ist `**` + `!worker/` + `!worker/**` und braucht für neue Worker-Dateien eine Handhabungsregel**~~ → die Datei ist inzwischen ausdifferenziert; die drei Zeilen sind nur noch die *erste* Hälfte, der Rest sind Re-Ausschlüsse. Siehe „`.dockerignore` am Root".

Offen:

8. **`SESSION_COOKIE_SECURE` ist nicht an `APP_ENV=production` gebunden.** `cookie_secure` existiert in `app/core/config.py` mit `AliasChoices("SESSION_COOKIE_SECURE", "COOKIE_SECURE", "cookie_secure")`, Default `false`. Das ist **absichtlich so gelassen**: eine automatische Bindung an Production würde dieselbe Compose-Datei im Klartext brechen — die Anmeldung wirkt erfolgreich und jeder Folge-Request scheitert mit 401, weil der Browser das Cookie ohne `Secure` zurücksendet. Eine Pflicht-Validierung in `validate_production_secrets()` wäre die spätere Härtung, **nicht gemacht**: die Anforderung steht stattdessen in `.env.example`, `docker-compose.yml` und `docs/server.md`. Wer sie erzwingen will, muss vorher klären, wie derselbe Stack lokal im Klartext laufen soll.
9. **`INTEGRATION_ENCRYPTION_KEY` zu rotieren zerstört alle gespeicherten Zugangsdaten.** Das `sa1.`-Präfix trägt keine Key-Id, es gibt also keinen Weg, eine mit einem neuen Schlüssel verschlüsselte Datei zu lesen. Kein offener Code-Mangel, sondern eine Eigenschaft des Formats — aber sie gehört hierher, weil `validate_production_secrets()` die Rotation beim Start nicht abfängt: der Startschutz greift nur bei Platzhaltern, nicht bei einem bewusst gesetzten anderen Schlüssel.

## `.dockerignore` am Root — Handhabungsregeln für den Worker-Kontext

Die Datei gilt **ausschließlich** für den Worker-Build (`docker-compose.yml:113` `context: .`, `dockerfile: worker/Dockerfile`); `backend` und `frontend` sind eigene Kontexte mit eigener `.dockerignore` und werden von hier nicht berührt.

  - **Der Worker hat keinen Importbedarf außerhalb von `worker/`.** `worker/main.py:1-10` importiert nur `json, logging, math, os, signal, threading, datetime, pathlib` und `psycopg`; `worker/healthcheck.py:1-4` nur `json, os, datetime, pathlib`; `worker/tests/test_healthcheck.py:1-7` zusätzlich `worker.healthcheck`. Es gibt **kein** `from app...` und keinen Import aus `backend/`. `worker/requirements.txt` ist eine Zeile: `psycopg[binary]==3.3.4`, installiert in `worker/Dockerfile:9-10` per `pip install` aus dem Netz.
  - **Eine neue Worker-Abhängigkeit braucht darum *keine* `.dockerignore`-Änderung**, sondern eine Zeile in `worker/requirements.txt`. Der Kontext liefert nur Quelltext; aufgelöst wird im Image. Das ist der Grund, warum `backend/` nicht in den Kontext muss: der Worker ist mit `psycopg` bewusst ein eigenständiges Mini-Paket, und `backend/` ist laut Verzeichnis-Tabelle die *einzige* Python-Package-Grenze.
  - **Eine neue Datei unter `worker/` braucht ebenfalls keine `.dockerignore`-Änderung** (`!worker/**` deckt sie ab) — aber sie braucht eine `COPY`-Zeile in `worker/Dockerfile`. Kontext ≠ Image: `worker/tests/` ist im Kontext, wird aber von `worker/Dockerfile:15` nicht kopiert. Deshalb hat der CI-Job zusätzlich ein `python -m py_compile worker/main.py worker/healthcheck.py`: `worker/main.py` importiert kein Test, ein Syntaxfehler darin käme sonst erst im Image-Build heraus.
  - **Der eigentliche Fund: `!worker/**` hat Host-Artefakte mitgenommen.** Die Werkzeuge aus dem Abschnitt „Befehle" laufen auf dem Host und erzeugen `worker/__pycache__/`, `worker/tests/__pycache__/`, `worker/.pytest_cache/`, `worker/.mypy_cache/`, `worker/.ruff_cache/` — und im ungünstigen Fall `worker/.venv/`. Ein bedingungsloses `!worker/**` nimmt alles davon wieder auf. Jetzt stehen die Ausschlüsse `**/__pycache__/`, `**/*.py[cod]`, `**/.pytest_cache/`, `**/.mypy_cache/`, `**/.ruff_cache/`, `**/.venv/`, `**/venv/`, `**/*.egg-info/`, `**/.coverage` sowie `**/node_modules/`, `**/.next/`, `**/.turbo/`, `**/out/` **hinter** der Re-Inklusion.
  - **Die Reihenfolge ist die eigentliche Regel, nicht der Inhalt.** In `.dockerignore` gewinnt die *letzte* passende Regel. Wer die Cache-Ausschlüsse über `!worker/**` zieht, holt alle sieben Lecks zurück; verifiziert. Umgekehrt ist die Reihenfolge kein Zufall: `**` muss erst, die `!worker/`-Re-Inklusion muss danach stehen, und jeder Re-Ausschluss muss ganz nach unten.
  - **`.env` und `data/` waren nur zufällig geschützt.** Vorher hing ihr Ausschluss allein am `**`-Catch-all. Ein späteres pauschales `!**` hätte `.env`, `.env.production` und `data/documents/`, `data/backups/` in den Kontext gezogen. Sie stehen jetzt explizit drin (`.git`, `.gitignore`, `.env`, `.env.*`, `data/`), damit eine künftige breitere `!`-Regel sie nicht mitnimmt. Verifiziert: mit `!**` bleiben sie draußen. `!.env.example` wurde bewusst **nicht** nachgezogen — der Worker-Build liest die Datei nicht, und ein Kontext aus `worker/` allein ist mehr wert.
  - **Braucht der Backup-Runner doch Backend-Code** (`pg_dump` als Datei, `httpx` für einen Upload), ist das *keine* Aufgabe für diese Datei. `!backend/app/` würde den Build nicht retten, ohne die Grenze zwischen den beiden Python-Bereichen aufzuweichen, und zöge den Backend-Quelltext in ein Image, das laut `docs/server.md` nur Worker-Code enthält. Richtig ist: Worker self-contained halten und die geteilte Logik in ein Paket legen, das der Worker-Kontext bewusst sieht — oder den geteilten Code in den Backend-Dienst, der ohnehin schon läuft. **Der planmäßige WebUntis-Abruf hat genau die zweite Möglichkeit gewählt**, siehe „Planmäßiger WebUntis-Abruf".

## Image-Bau und Build-Kontexte

- **`backend/entrypoint.sh` ist der einzige Migrations-Start des Backends.** `backend/Dockerfile:18` ist nur noch `CMD ["sh", "/app/entrypoint.sh"]`; `docker-compose.yml` hat **kein** `entrypoint:`-Override und **keinen** Bind-Mount des Skripts mehr. Vorher lief unter Compose nur der Bind-Mount, die CMD-Zeile war toter Text — eine Änderung daran hätte nichts bewirkt. Das ist die genaue Desync-Klasse, die das ToDo meldete.
- **Der Umzug nach `backend/` war kein Geschmack, sondern eine Folge der Root-`.dockerignore`.** Die Datei lässt alles außer `worker/` aus dem Kontext; ein Skript im Root hätte also nur per Bind-Mount funktioniert — und damit genau die zweite Quelle der Wahrheit wiederhergestellt, die abgeschafft werden sollte. Im Backend-Kontext ist es eine ganz normale `COPY . .`-Datei.
- **Das Skript macht `cd "$(dirname "$0")"`, damit `alembic.ini` unabhängig vom Arbeitsverzeichnis gefunden wird**, und es läuft als `sh <pfad>`, nicht direkt: ein Checkout unter Windows garantiert keine Ausführungsrechte. `set -eu` lässt Uvicorn bewusst nicht starten, wenn das Upgrade scheitert — ein Backend gegen ein nicht migriertes Schema ist schlimmer als kein Backend.
- **`.gitattributes` mit `*.sh text eol=lf` ist Pflicht, nicht Kosmetik.** Ohne sie legt `core.autocrlf=true` beim Windows-Checkout CRLF in die Arbeitskopie, und `dash` bricht an `set -eu` mit `Illegal option -` ab — das Backend startet dann gar nicht, und der Fehler zeigt auf das falsche System.
- **Neue Migration: neue Revision in `backend/alembic/versions/`.** Es sind **6**, keine neue dazugekommen: `LearningProgress.confidence`/`last_reviewed_at` und die Wochenziel-Präferenz gab es schon, deshalb brauchte die Fortschritts-Welle keine.

## CI und die akzeptierte Baseline

`.github/workflows/ci.yml` läuft bei jedem PR, 7 Jobs: `frontend` (Matrix `test`/`lint`/`typecheck`), `frontend-build`, `backend` (Matrix `pytest`/`ruff`/`mypy`), `worker`, `arm64-wheels`, `compose-config`, `worker-image`. `concurrency` mit `cancel-in-progress` bricht überholte Läufe ab, `fail-fast: false` in beiden Matrizen lässt jeden Check selbst berichten.

- **Die interessanteste Entscheidung ist die Baseline, nicht die Jobliste.** `npm test` und `npm run lint` sind rot, und das bleibt so — aber über einen **eigenen Prüfer**, nicht per `continue-on-error`. Beide Werkzeuge schreiben zusätzlich einen JSON-Report neben ihre normale Ausgabe, und `.github/ci/check-known-failures.mjs` **besitzt den Exit-Code des Schritts**. `|| true` gilt nur dem Exit-Code des Werkzeugs, nie dem Schritt.
- **Die Liste ist enger als „Lint auslassen":** `frontend-test` nennt **einen** vollständigen Testnamen, `frontend-lint` **zwei** (Datei, Regel)-Paare mit einer Obergrenze je Datei (`lib/schemas.ts` 7 + `lib/types.ts` 15 = 22). Die Obergrenze ist der Punkt: ein 23. `any` in derselben Datei fällt den Job, obwohl die Datei erlaubt ist. Ein **fehlender, leerer oder kaputter Report ist ein harter Fehler** — genau der Fall, den ein pauschales `continue-on-error` versteckt, weil ein abgestürztes Werkzeug dann wie „nichts ist schiefgelaufen" aussieht. Damit fängt der Report-Durchgang auch die Abstürze, die das `|| true` am Exit-Code des Werkzeugs durchlässt.
- **Ein alter Eintrag ist eine Warnung, kein Fehler.** Verschwindet der bekannte Fehler, warnt der Prüfer (`STALE`) und fordert zum Entfernen auf: ein toter Eintrag ist ein Loch, das die nächste Regression verstecken kann.
- **Worker-Tests laufen aus dem Repo-Root**, nicht aus `worker/` — `worker/tests/test_healthcheck.py:7` importiert `worker.healthcheck`. Der Job hat deshalb kein `working-directory`. Und er macht zusätzlich `py_compile`, weil `worker/main.py` von keinem Test importiert wird.
- **Die Backend-Installation ist `pip install -r requirements.txt -e ".[dev]"`**: Laufzeit aus der Datei, die auch `backend/Dockerfile` nutzt, Werkzeuge aus dem vorhandenen `[dev]`-Extra — so wird kein zusätzlicher Lockfile eingeführt. Nebenwirkung auf einer Developer-Maschine: `backend/*.egg-info/`, und `.gitignore` deckt `*.egg-info/` **nicht** ab (`.dockerignore` schon). Wer lokal `pip install -e` macht, sieht die Dateien also als unversioniert.
- **`compose-config` nutzt `docker compose config --quiet`** und wirft Platzhalter über `env:`. Ohne `--quiet` landeten die interpolierten Zugangsdaten im Log.
- **`worker-image` ist der einzige Build, der von der Root-`.dockerignore` abhängt.** Eine Regression dort bricht das Image, nicht einen Linter — deshalb ist es einen Job wert.
- **ARM64 ist ein eigener Workflow** (`.github/workflows/arm64.yml`), `workflow_dispatch` plus wöchentlich Mo 04:17 UTC: unter QEMU wäre jedes `RUN` emuliert, und `npm ci` + `next build` emuliert gehört nicht in den Weg jedes PRs. Am Ende steht `docker image inspect --format '{{.Architecture}}'`, denn ein Build, der nichts prüft, beweist nur Exit 0. `load: true` und `provenance: false`/`sbom: false` sind dafür nötig — sonst lässt sich das Image nicht laden bzw. wird der Export mehrdeutig.
- **Zwei `--platform`-Tags im `arm64-wheels`-Job sind nötig, nicht überflüssig:** `psycopg-binary==3.3.4` liefert nur `manylinux_2_27/2_28`, `pydantic-core` nur `manylinux_2_17`, und pip akzeptiert ein Wheel für eine Ziel-GLibc je Tag nur in eine Richtung. Mit einem Tag scheitert jeweils eine der beiden Familien. **`pgvector` ist kein Plattformproblem** — `pgvector-0.5.0-py3-none-any.whl` ist reines Python; architekturabhängig sind `psycopg-binary` und das `pgvector/pgvector`-Manifest.
- **Was die Workflows noch nicht beweisen:** nie auf einem Runner gelaufen, kein Image gebaut, TLS/Restore/ARM64-Ausführung ungetestet. Der Build-Check beweist „die Dockerfiles setzen sich für arm64 zusammen", nicht „der Stack läuft auf einem Raspberry Pi".

## Stolperfallen

- `docker compose down -v` löscht die Named Volumes `pgdata` und `worker-status` — nur nach bestätigter Sicherung.
- `docker compose config` interpoliert Zugangsdaten; Ausgabe nicht teilen oder loggen.
- Änderungen an `POSTGRES_USER`/`PASSWORD`/`DB` wirken **nicht** auf ein bereits initialisiertes `pgdata`-Volume.
- Der Worker-Healthcheck ist absichtlich streng: nur `status == "running"` **und** `database == "healthy"` **und** ein frischer, zeitzonenbehafteter Heartbeat gelten als gesund.
- `ADMIN_PASSWORD` rotiert das Passwort eines bereits existierenden Bootstrap-Admins nicht automatisch.
- Host-Ports sind fest auf `127.0.0.1:3000/5432/8000` gebunden; nur `FRONTEND_PORT` ist änderbar.
- **`pg_dump` allein ist kein Backup.** Die hochgeladenen Dokumente liegen als Dateien unter `DOCUMENT_STORAGE_PATH`; `documents.storage_path` verweist darauf, und ein Dump ohne diese Dateien hinterlässt eine Datenbank, deren Dokumentenliste ins Leere zeigt.
- Kein Backup-Runner enthalten — `BACKUP_SCHEDULE`/`BACKUP_RETENTION_DAYS` sind reserviert und werden nicht ausgeführt, die iCalendar-Variablen ebenfalls. Ausführbaren Code haben inzwischen `/v1/integrations`, `/v1/files`, `/v1/documents` und `/v1/progress`.
