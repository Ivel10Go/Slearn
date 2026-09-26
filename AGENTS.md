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

- `npm.cmd test` → **1 Fehlschlag**: `lib/types.test.ts` › "lehnt fehlerhafte API-Daten ab" (18 grün).
- `npm.cmd run lint` → **23 Fehler**, ausschließlich `@typescript-eslint/no-explicit-any` in `lib/schemas.ts` und `lib/types.ts`. Absichtliche Arbeitsumgehung, kommentiert in `lib/types.ts:8`: die Zod-Schemas sind `z.object({}).passthrough()`-Stubs, die nichts validieren. `typecheck` ist dagegen sauber.
- `python -m pytest` → **27 grün**, `ruff` und `mypy` sauber.
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
- Der `test`-Endpoint testet **keine echte Verbindung** — er prüft nur `missing_fields` und gibt danach `status="pending"` zurück, weil die Adapter (WebUntis, Ollama) nicht implementiert sind. Die UI muss das als Pending darstellen, nicht als Fehler.
- `credentials: {}` im PATCH leert die gespeicherten Zugangsdaten und setzt `status` auf `configured`.

## Verifizierte Config-Lücken (nicht in README dokumentiert)

Geschlossen:

1. ~~`INTEGRATION_ENCRYPTION_KEY` fehlt in Compose~~ → jetzt in `docker-compose.yml` (Pflicht, `${INTEGRATION_ENCRYPTION_KEY:?...}`), `.env.example` und README-Tabelle.
2. ~~`cryptography` fehlt in `pyproject.toml`~~ → jetzt in `[project] dependencies` gepinnt.

Offen:

3. **README nennt 2 Alembic-Revisionen, es gibt 3** (`20260925_0003_integration_credentials`).
4. **Migrations-Start doppelt definiert**: `backend/Dockerfile:15` (CMD) und `backend-entrypoint.sh`. Compose überschreibt per `entrypoint:` und bindet das Skript read-only ein — Änderungen am Dockerfile-CMD wirken unter Compose nicht.
5. **Root-`.dockerignore` ist `**` + `!worker/` + `!worker/**`** und gilt nur für den Worker-Build (Kontext `.`). Backend und Frontend haben eigene Kontexte und eigene `.dockerignore`. Braucht der Worker neue Dateien, muss die Root-`.dockerignore` erweitert werden.

## Stolperfallen

- `docker compose down -v` löscht die Named Volumes `pgdata` und `worker-status` — nur nach bestätigter Sicherung.
- `docker compose config` interpoliert Zugangsdaten; Ausgabe nicht teilen oder loggen.
- Änderungen an `POSTGRES_USER`/`PASSWORD`/`DB` wirken **nicht** auf ein bereits initialisiertes `pgdata`-Volume.
- Der Worker-Healthcheck ist absichtlich streng: nur `status == "running"` **und** `database == "healthy"` **und** ein frischer, zeitzonenbehafteter Heartbeat gelten als gesund.
- `ADMIN_PASSWORD` rotiert das Passwort eines bereits existierenden Bootstrap-Admins nicht automatisch.
- Host-Ports sind fest auf `127.0.0.1:3000/5432/8000` gebunden; nur `FRONTEND_PORT` ist änderbar.
- Kein Backup-Runner enthalten — `BACKUP_SCHEDULE`/`BACKUP_RETENTION_DAYS` sind reserviert und werden nicht ausgeführt. KI-, WebUntis- und iCalendar-Variablen sind ebenfalls reserviert (nur `/v1/integrations` und `/v1/files` haben Code).
