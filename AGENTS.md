# AGENTS.md

Lokal betreibbarer Docker-Compose-Stack: Next.js 16 Frontend, FastAPI Backend, PostgreSQL 15 + pgvector, schlanker Python-Worker. `README.md` ist die ausführliche Referenz (Architektur, API-Tabelle, alle Env-Variablen) — hier nur schwer erkennbares Kontextwissen.

## Verzeichnisse

| Pfad | Inhalt |
| --- | --- |
| `frontend/` | Next.js 16 (App Router, Turbopack, React 19, Tailwind v4). Einziger npm-Package-Boundary. |
| `backend/` | FastAPI + SQLAlchemy + Alembic. Einziger Python-Package-Boundary, `app/` unter `backend/`. |
| `worker/` | Nur DB-Healthcheck + Status-Heartbeat. Kein HTTP-Port, keine Jobs. |
| `postgres/init/` | pgvector-Aktivierung bei neuer DB. |
| `data/` | Gitignoriert (bis auf `.gitkeep`). |

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

Kein CI, keine Pre-Commit-Hooks, kein Root-`package.json`/Task-Runner. Jeder Bereich wird einzeln geprüft.

## ⚠️ `next build` braucht `--webpack`

`npm.cmd run build` (Turbopack) bricht reproduzierbar mit `TurbopackInternalError: ModuleGraph::from_graphs was canceled` ab — **auch bei vollgängigem C:-Laufwerk**. Es ist ein Turbopack-Bug, kein Code- und kein Plattenproblem: `npx.cmd next build --webpack` läuft durch und prerendert alle Routen. Panic-Log: `%TEMP%\next-panic-*.log`.

Für Lint-/Type-Caches im Notfall auf ein anderes Laufwerk legen, falls C: wieder randvoll ist:
`$env:RUFF_CACHE_DIR`, `$env:MYPY_CACHE_DIR`, `pytest --basetemp=<pfad> -p no:cacheprovider`.
`E:` hat ~923 GB frei. `frontend/.next` und `frontend/node_modules` sind reine Git-Ignore-Artefakte.

## Baseline-Status (vor jeder Änderung)

Nicht als Regressionen fehlinterpretieren:

- `npm.cmd test` → **1 Fehlschlag**: `lib/types.test.ts` › "lehnt fehlerhafte API-Daten ab" (37 grün).
- `npm.cmd run lint` → **22 Fehler**, ausschließlich `@typescript-eslint/no-explicit-any` in `lib/schemas.ts` und `lib/types.ts`. Absichtliche Arbeitsumgehung, kommentiert in `lib/types.ts:8`: die Zod-Schemas sind `z.object({}).passthrough()`-Stubs, die nichts validieren. `typecheck` ist dagegen sauber.
- `python -m pytest` → **126 grün**, `ruff` und `mypy` sauber.
- Worker-Tests: 4 grün.

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
- **Theme-Farben an drei Stellen hartkodiert**: `app/layout.tsx:23-27` (`viewport.themeColor`) und `app/providers.tsx:14` (`#fafafa` / `#0a0a0a`). Palette ändern ⇒ alle drei synchronisieren, sonst desyncen Meta-Tag und UI.
- **Design ist monochrome und komplett eckig**: alle `--radius*`-Tokens in `globals.css` stehen auf `0px`. Das schaltet `rounded-*` global ab — wer eine Rundung braucht, muss das Token ändern oder explizit eine Arbitrary-Value-Klasse setzen. Es gibt bewusst keine Farbklassen, Gradienten, Schatten oder `backdrop-blur`; Struktur tragen Haarlinien. Farbige Zustände werden über Gewicht, Unterstreichung und deutsche Labels unterschieden.
- **Strikte CSP in `next.config.ts:4-17`**: `script-src 'self' 'unsafe-inline'` (+ `'unsafe-eval'` nur in Dev), `style-src 'self' 'unsafe-inline'`, `img-src 'self' data: blob:`, `font-src 'self' data:`, `connect-src 'self'` in Produktion. Also: keine Google-Fonts-Link, keine CDN-Bilder/Skripte, keine externen Fonts zur Laufzeit.
- `viewportFit: "cover"`; die mobile Bottom-Nav braucht `env(safe-area-inset-bottom)`.
- **Vitest läuft nur mit `include: ["lib/**/*.test.ts"]` und `environment: "node"`** (`vitest.config.mts`). Komponenten-/DOM-Tests werden **stillschweigend nicht ausgeführt** — nicht erwarten, dass sie greifen.
- Service Worker `public/sw.js` ist network-first mit Fallback auf `/offline/`, `CACHE_NAME = "lernraum-static-v3"`. Bei Änderung der App-Shell den Cache-Namen bumpen, sonst sehen Nutzer eine veraltete Shell.
- Navigation ist **Anker auf einer einzigen Seite** (`/#overview`, `/#schedule`, `/#study-tasks`, `/#calendar`, `/#usage`) plus `/settings` — keine eigenen Routen. Aktivzustand über `isNavigationHrefActive` in `lib/navigation.ts` (hash- und pathname-abhängig, `usePathname` + `hashchange`).
- `@/*` → Wurzel von `frontend/`. `cn()` = `clsx` + `tailwind-merge`.
- **UI-Texte sind deutsch** (`<html lang="de">`), auch Fehlermeldungen im Proxy-Route-Handler und die Testnamen.
- Das eigentliche Dashboard ist `components/dashboard/dashboard-view.tsx` (~730 Zeilen), eingehängt in `app/page.tsx`; Shell/Nav in `components/app-shell.tsx`.

## Backend-Konventionen

- ruff: `line-length = 120`, `target-version = "py312"`, Regeln `E4,E7,E9,F,I` — **`I` erzwingt Importsortierung**, die häufigste Lint-Ursache.
- mypy: `warn_return_any`, `no_implicit_optional`, `check_untyped_defs` — ein `-> None` fehlt nicht zufällig.
- pytest: `asyncio_mode = "auto"` (async-Tests ohne Marker), `testpaths = ["tests"]`. Es gibt nur `tests/conftest.py` + `tests/test_backend.py`.
- **Backend-Tests brauchen kein Postgres**: `conftest.py` legt pro Test eine SQLite-DB in `tmp_path` an, fährt `alembic upgrade head` und überschreibt `get_db` per `dependency_overrides`.
- **FastAPI-Dependencies als `Annotated`-Alias, nie zusätzlich mit `= Depends()`**: `deps.py` definiert `CurrentUser`/`DatabaseSession`/`AppSettings` bereits als `Annotated[X, Depends(...)]`. Ein zusätzliches `= Depends()`-Default lässt FastAPI mit `AssertionError: Cannot specify Depends in Annotated and default value together` abstürzen — **beim Import von `app.main`, also startet der ganze Container nicht**. Parameter mit `Annotated`-Alias daher immer ohne Default deklarieren; ein Parameter mit Default (z. B. `file: UploadFile = File(...)`) muss **hinten** stehen.
- **Neue Router müssen in `create_app()` registriert werden** (`app/main.py`), sonst sind alle Routen 404 — der Import in der Funktion allein registriert nichts. Das hat den kompletten `/api/v1/integrations`-Block initially unerreichbar gemacht.
- Migrationen immer als neue nummerierte Revision in `backend/alembic/versions/`; `backend-entrypoint.sh` führt `alembic -c alembic.ini upgrade head` aus, Uvicorn startet erst bei Erfolg.
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
- Ein logisch fehlgeschlagener Sync antwortet mit **HTTP 200** und `ok: false` (wie `/test`). Nur Session-, Ownership- und Key-Fehler ergeben 4xx/503. Reihenfolge: `missing_fields` → `enabled` → Key → `check_integration()` → `import_timetable()`.
- **`getTimandis`… die Methode heißt `getTimetable`** und wird über `{base_url}/WebUntis/jsonrpc.do?school={school}` aufgerufen. Der `params`-Wert ist ein **Objekt**, kein positionelles Array — im minifizierten `main.js` sieht man die generische Array-Dekodierung, aber der interne Aufruf benutzt `SchoolUtils/WebUntis/src/base.ts` als Objekt. Das ist live gegen Lessing-Gymnasium verifiziert, nicht nur aus dem Bundle abgeleitet: mit Array liefert das Portal einen Fehler.
- Pflichtfelder in `options`: `element={id: personId, type: personType}` (Schüler `5`, Klasse `1`, Lehrer `2`, Fach `3`, Raum `4`), `startDate`/`endDate` als **`YYYYMMDD` ohne Trennzeichen**, `id` als Unixzeit in ms, dazu die `show*`-Flags und `classFields`/`roomFields`/`subjectFields`/`teacherFields` = `["id","name","longname","externalkey"]`.
- Antwortobjekt: `id`, `klasse`/`teachers`/`rooms`/`subjects`, `timeGrid`. Eine Einheit: `id`, `date`, `startTime`/`endTime` (`HHMM`), Relationsarrays `kl`/`te`/`su`/`ro`, `lsnumber`, `activityType`, optional `lstext`/`code`. Fehlende Pflichtfelder ⇒ Eintrag wird übersprungen und in `skipped_entries` gezählt, der Import bricht **nicht** ab.
- **Zeitannahme:** WebUntis liefert Schul-Ortszeit. `timetable_import` interpretiert `startTime`/`endTime` in `current_user.timezone` und schreibt über `as_utc()` UTC. `07:45 Europe/Berlin` ⇒ `05:45+00:00`. Verifiziert: `09:35` ⇒ `07:35+00:00`.
- **`db.flush()` nach den Stammdaten-Upserts ist Pflicht, nicht Kosmetik.** Ohne Flush haben neue `Subject`/`Teacher`/`Room`-Zeilen kein `id`, und die Lesson-Verweise bleiben `None` — der Import lief mit 60 Lessons, aber alle ohne Fach, Lehrer und Raum. Genau dieser Fehler ist passiert.
- Stammdaten werden anhand `source == "webuntis"` + `external_id` wiederverwendet, sonst über den **Namen** — sonst erzeugt der Import Dubletten, wenn man ein Fach manuell angelegt hat. Fach/Lehrer nehmen `longname`, Räume `name` (`longname` ist dort teils der Klassenname).
- Der Replace löscht nur Lessons mit derselben `sync_source_id` im Fenster. Manuelle Lessons und Lessons außerhalb des Fensters bleiben unangetastet; das ist mit einem Test abgesichert.
- `code` und `lsnumber` werden nicht gespeichert, weil `Lesson` keine Felder dafür hat. `activityType` wird geparst, aber nicht persistiert; der Titel kommt aus `lstext`.
- Der Sync läuft **nicht** automatisch. Es gibt keinen Scheduler-Job; `worker/` macht ausschließlich DB-Healthcheck und Heartbeat. Auslöser ist der Button **Stundenplan aktualisieren** in den Einstellungen, der `syncIntegration()` in `frontend/lib/api.ts` aufruft.
- `IntegrationSyncResult` hat zusätzlich zu den Test-Feldern `imported_lessons`, `subjects`, `teachers`, `rooms`, `skipped_entries`, `window_start`, `window_end`, `last_sync_at`. Der Frontend-Zod-Schema `integrationSyncResultSchema` in `lib/schemas.ts` muss diese Felder spiegeln, sonst `.parse()` strippt sie stillschweigend.
- **`_resolve` aktualisiert bestehende Stammdaten nicht.** Ein gleichnamiger Treffer wird wiederverwendet, aber `source`/`external_id`/`name` bleiben unverändert. Deshalb trägt der Importer `Subject.short_name` **nachträglich** nach (`if not found.short_name`), sonst bliebe ein vor dem Feld importiertes Fach dauerhaft ohne Kürzel. Von Hand gepflegte Kürzel werden dabei nicht überschrieben.
- **`db.flush()` nach den Stammdaten-Upserts ist Pflicht, nicht Kosmetik.** Ohne Flush haben neue `Subject`/`Teacher`/`Room`-Zeilen kein `id`, und die Lesson-Verweise bleiben `None` — der Import lief mit 60 Lessons, aber alle ohne Fach, Lehrer und Raum. Genau dieser Fehler ist passiert.

## Willkommenssatz (`GET /api/v1/greeting`)

- `app/services/greeting.py` baut den Satz aus `DashboardResponse`, nicht aus eigenen Queries. `_next_lesson()` rechnet **in der Nutzerzeitzone**: die Lektionen liegen als UTC vor, und `07:45 Europe/Berlin` darf nicht als `06:45` im Satz stehen. `zone: ZoneInfo` ist deshalb ein Pflichtparameter von `build_fallback_greeting` und `build_prompt` — sonst rutscht man unbemerkt in UTC.
- **Ohne Provider kommt `source: "fallback"`, nie ein Fehler.** Kein Provider, kein Schlüssel, ConnectError, 401, leere Antwort: alles führt zum deterministischen Satz. Die Oberfläche zeigt die Herkunft als Badge an, damit niemand einen Zufallssatz für eine KI-Aussage hält.
- **Ein Modellaufruf pro Tag, nicht pro Seitenaufruf.** `greeting_cache` hält je Nutzer und `generated_on` genau eine Zeile (`uq_greeting_cache_user_day`). `POST /api/v1/greeting/refresh` überschreibt sie. Ohne Cache kostete jeder Reload Token und 1–5 s Wartezeit.
- `AiProviderError` wird **gefangen** und in `logger.warning` geschrieben, nicht weiter gereicht. Eine kaputte KI-Konfiguration darf das Dashboard nicht lahmlegen.
- Der Client hat **keinen Demo-Fallback**, anders als `getDashboard`. Ein erfundener Begrüßungssatz wäre nicht als solcher erkennbar.

## Verifizierte Config-Lücken (nicht in README dokumentiert)

Geschlossen:

1. ~~`INTEGRATION_ENCRYPTION_KEY` fehlt in Compose~~ → jetzt in `docker-compose.yml` (Pflicht, `${INTEGRATION_ENCRYPTION_KEY:?...}`), `.env.example` und README-Tabelle.
2. ~~`cryptography` fehlt in `pyproject.toml`~~ → jetzt in `[project] dependencies` gepinnt.
3. ~~`INTEGRATION_TIMEOUT_SECONDS` nicht in Compose~~ → jetzt mit Default `10` weitergereicht.
4. ~~`httpx` fehlt als Runtime-Dependency~~ → steht in `[project] dependencies` und `requirements.txt`.

Offen:

5. **README nennt 2 Alembic-Revisionen, es gibt 5** (`0003` Integration-Credentials, `0004` `subjects.short_name` + `lessons.is_cancelled`, `0005` `greeting_cache`).
6. **Migrations-Start doppelt definiert**: `backend/Dockerfile:15` (CMD) und `backend-entrypoint.sh`. Compose überschreibt per `entrypoint:` und bindet das Skript read-only ein — Änderungen am Dockerfile-CMD wirken unter Compose nicht.
7. **Root-`.dockerignore` ist `**` + `!worker/` + `!worker/**`** und gilt nur für den Worker-Build (Kontext `.`). Backend und Frontend haben eigene Kontexte und eigene `.dockerignore`. Braucht der Worker neue Dateien, muss die Root-`.dockerignore` erweitert werden.

## Stolperfallen

- `docker compose down -v` löscht die Named Volumes `pgdata` und `worker-status` — nur nach bestätigter Sicherung.
- `docker compose config` interpoliert Zugangsdaten; Ausgabe nicht teilen oder loggen.
- Änderungen an `POSTGRES_USER`/`PASSWORD`/`DB` wirken **nicht** auf ein bereits initialisiertes `pgdata`-Volume.
- Der Worker-Healthcheck ist absichtlich streng: nur `status == "running"` **und** `database == "healthy"` **und** ein frischer, zeitzonenbehafteter Heartbeat gelten als gesund.
- `ADMIN_PASSWORD` rotiert das Passwort eines bereits existierenden Bootstrap-Admins nicht automatisch.
- Host-Ports sind fest auf `127.0.0.1:3000/5432/8000` gebunden; nur `FRONTEND_PORT` ist änderbar.
- Kein Backup-Runner enthalten — `BACKUP_SCHEDULE`/`BACKUP_RETENTION_DAYS` sind reserviert und werden nicht ausgeführt. KI- und iCalendar-Variablen sind ebenfalls reserviert (nur `/v1/integrations` und `/v1/files` haben Code).
