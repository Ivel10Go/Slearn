# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`AGENTS.md` is the main contributor guide. It is imported below and holds the hard-won pitfalls for each feature area. `README.md` is only the entry point; the full reference lives in `docs/` (`server.md` operations/backup/HTTPS/secret rotation, `api.md` API table, `konfiguration.md` every env variable, `entwicklung.md` tests/CI, `desktop.md` desktop app). `PLAN.md` is the feature TODO list. Every one of these docs, the code comments, and the test names is in **German**. The UI is bilingual (German/English): write new UI strings in German and wrap them in `t()` from `frontend/lib/i18n.ts`, then add the English entry to `frontend/lib/i18n/en.ts` - `lib/i18n.test.ts` fails otherwise. See "Mehrsprachigkeit" in AGENTS.md.

@AGENTS.md

## Where AGENTS.md has drifted (verify before trusting)

Several feature waves have been merged since AGENTS.md was last updated: grades, calendar sync, notifications, rewards, local models, speech, and AI routing. Before quoting any of these points, check them against the code:

- **The worker is not healthcheck-only any more.** It also runs the `pg_dump` backup runner (`worker/backup.py`, `worker/env.py`, `worker/tests/test_backup.py`). The CI compile check covers all four files: `python -m py_compile worker/main.py worker/healthcheck.py worker/env.py worker/backup.py`.
- **There are 16 Alembic revisions, not 6** (`0007` grades through `0010` rewards, `0011` assistant memories, `0012` training examples, `0013` subject colors, `0014` materials, `0015` homework link for the week planner, `0016` greeting language). Run `Get-ChildItem backend/alembic/versions`.
- **Backend tests now span** `tests/test_backend.py`, `tests/test_rewards.py`, `tests/test_assistant.py`, `tests/test_training.py`, `tests/test_planner.py` and `tests/test_language.py`.
- **`test_streak_und_wochenziel_kommen_aus_den_lernsessions` is date-dependent**: it fails on some weekdays (verified on a Thursday, also on an unchanged `HEAD`). Do not read it as a regression.
- **The baseline test and lint counts are not stable.** The source of truth is `.github/ci/known-failures.mjs`, which records the accepted failing test ID and the per-file `any` ceilings. Change it whenever the real output changes.
- **The backend lifespan starts two independent threads:** `IntegrationSyncScheduler` (WebUntis, opt-in via `WEBUNTIS_SYNC_ENABLED`) and `ReminderScheduler` (`app/services/notification_scheduler.py`). `create_app()` also always builds `app.state.model_downloads` (`ModelDownloadManager`) and `app.state.speech` (`SpeechService`). Both exist even when no model or runtime is present.
- **The frontend has real routes in addition to the dashboard anchors:** `/lernen`, `/faecher`, `/dokumente`, `/assistent`, `/settings`, and `/offline`.

## Big picture

```text
Browser ──/api/backend/v1/*──▶ Next.js route handler (app/api/backend/[...path]/route.ts, lib/proxy.ts)
                                   └─▶ ${BACKEND_INTERNAL_URL}/api/v1/*  (FastAPI, app.main:app)
                                           └─▶ PostgreSQL 15 + pgvector
worker (no HTTP, no backend imports) ──psycopg──▶ Postgres: heartbeat, healthcheck, pg_dump backups
```

- **Backend layering:** `app/api/<area>.py` contains thin routers that use the `Annotated` deps from `app/api/deps.py`. `app/services/<area>.py` holds all of the logic and is what the tests exercise. `app/db/*.py` holds the SQLAlchemy models, split by domain (`academic`, `planning`, `identity`, `notify`, `rewards`, …). `app/schemas/<area>.py` holds the Pydantic models. A new router does nothing until it is added with `include_router` in `create_app()` (`app/main.py`).
- **Every `/api/v1` route except health** is scoped to the session user. If a resource belongs to another user, return 404, not 403.
- **Credentials for integrations** (WebUntis, AI provider, documents, calendars) are encrypted with `app/core/crypto.py` and are only ever decrypted inside the services.
- **AI calls** go through a single path: local runtime → online provider → deterministic fallback (`app/services/ai_routing.py`, `ai_provider.py`, `local_inference.py`). Responses report where the answer came from (`local`, `provider`, or `fallback`), and the UI shows that source.
- **Frontend data flow:** `lib/api.ts` (fetch through the proxy) → `lib/schemas.ts` (Zod; some schemas are still `passthrough` stubs) → `lib/use-*.ts` hooks → components under `components/{dashboard,manage,settings,speech,ui}`. Only `lib/**/*.test.ts` runs under vitest.

## Running single tests

```powershell
# Backend (cwd = backend/)
python -m pytest tests/test_backend.py -k "<substring of test name>"
python -m pytest tests/test_rewards.py::<test_function>

# Frontend (cwd = frontend/)
npx.cmd vitest run lib/progress.test.ts
npx.cmd vitest run -t "<German test name substring>"

# Worker (cwd = repo root)
python -m unittest worker.tests.test_backup
```

Full stack: `docker compose up --build`. Compose needs `.env` with the required variables, including `INTEGRATION_ENCRYPTION_KEY`. Copy `.env.example` to start.
