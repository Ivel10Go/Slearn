<div align="center">

<img src="logo/faultier-logo-A1.svg" alt="Lernraum sloth logo" width="120" />

# Lernraum

**Your whole school life in one calm place — with an AI study buddy that can run entirely on your own computer.**

**English** · [Deutsch](README.de.md)

![Version](https://img.shields.io/badge/version-1.1.0-111111)
![Platforms](https://img.shields.io/badge/desktop-Windows%20%7C%20macOS-111111)
![Server](https://img.shields.io/badge/server-Docker%20Compose-111111)
![Local AI](https://img.shields.io/badge/AI-local%20first-111111)

</div>

---

Lernraum ("learning space") brings timetable, homework, exams, appointments, study plans, grades and study time together. It can pull your timetable from WebUntis, plan your week around your lessons, and answer questions about your own notes and worksheets — with an online AI provider, a model on your own machine, or no AI at all.

## Highlights

| | |
| --- | --- |
| 📅 **Timetable** | Import from WebUntis, read from a PDF or photo, or enter by hand. Cancelled lessons are marked. |
| ✅ **Homework, exams, events** | Everything that's due, in one overview. Syncs with iCalendar, Google Calendar and Apple Calendar (CalDAV). |
| 🗓️ **Week planner** | Turns lessons, open tasks, homework, exams and due reviews into a concrete plan for the week. Deterministic, respects deadlines, daily budget and dependencies. |
| 🧠 **Exam generator** | Creates a chain of study tasks for an exam. Your weakest topic is scheduled first, because it needs the most lead time. |
| 📈 **Progress** | Five confidence levels per topic with fixed review intervals (1 / 3 / 7 / 14 / 30 days), study streaks, a weekly goal in minutes, and time per subject. |
| 🎓 **Grades** | Weighted averages per subject, a simple trend line, and a projection of what you still need to pass. |
| 🏅 **Rewards** | Points for real studying on a topic, not for ticking boxes. They never affect grades, priorities or the AI. |
| 📄 **Documents** | Upload notes, worksheets and solutions as text, PDF or photos. Scans are transcribed by a local vision model and sorted by type, subject, topic and date. |
| 🦥 **Study assistant** | A chat that knows your schedule, tasks, documents and learning profile. It suggests entries ("exam next Thursday"), quizzes you, and remembers facts you tell it. |
| 🎙️ **Voice** | Push-to-talk, read-aloud answers, and a hands-free conversation mode (whisper.cpp + Piper, offline). |
| 🔧 **Personal fine-tuning** | Optional, opt-in LoRA training on your own conversations so the assistant adapts to your style. Runs on NVIDIA GPUs or Apple Silicon. |
| 🌍 **Bilingual** | German and English throughout: UI, assistant, greeting, voice. |

A full tour of every feature (in German) is in **[docs/funktionen.md](docs/funktionen.md)**.

## Privacy by design

- **Local first.** With the desktop app and local models, nothing leaves your computer.
- **Every sharing option is off by default:** grades to an online AI, writing to external calendars, training on conversations or documents.
- **Encrypted credentials.** Logins for WebUntis, calendars and AI providers are stored with AES-GCM and only decrypted inside the backend.
- **Honest AI.** Every AI answer shows where it came from: `local`, `provider` or `fallback`. If no AI is available, the app still works.
- **Your data, your call.** Export everything (GDPR Art. 15/20) or delete your account completely (Art. 17).

## Two ways to run it

| | Desktop app | Server |
| --- | --- | --- |
| For | one person on their own computer | self-hosted, e.g. on a home server |
| Install | run the installer, enter your name | Docker Compose |
| Sign-in | none | username and password |
| Data | SQLite in your user folder | PostgreSQL 15 + pgvector |
| Local AI | built in: downloads llama.cpp, whisper.cpp and Piper itself | bring your own (Ollama, LM Studio, …) |

### Desktop app

Download the installer for your system from the [latest release](https://github.com/Ivel10Go/School-assistant/releases/latest):

- **Windows:** `Lernraum-Setup-<version>.exe`
- **macOS:** `Lernraum-<version>-arm64.dmg` (Apple Silicon) or `-x64.dmg` (Intel)

On first launch Lernraum only asks for your name. The app updates itself from GitHub Releases and backs up the database before each update.

> **Unsigned builds:** on Windows choose *More info → Run anyway*; on macOS open the app once with *right-click → Open*.

Your data lives in `%APPDATA%\Lernraum` (Windows) or `~/Library/Application Support/Lernraum` (macOS). To back it up, quit Lernraum and copy that folder.

### Server with Docker

```bash
cp .env.example .env
# fill in POSTGRES_*, DATABASE_URL, SECRET_KEY, INTEGRATION_ENCRYPTION_KEY, ADMIN_PASSWORD
docker compose up -d --build
```

Then open <http://localhost:3000> and sign in with `ADMIN_USERNAME` (default `admin`) and `ADMIN_PASSWORD`.

> Don't change `INTEGRATION_ENCRYPTION_KEY` after the first start, or stored credentials become unreadable.

Operations, backups and HTTPS: [docs/server.md](docs/server.md) · All variables: [docs/konfiguration.md](docs/konfiguration.md)

## Architecture

```text
Browser ──/api/backend/v1/*──▶ Next.js (proxy)
                                   └─▶ FastAPI backend /api/v1/*
                                           └─▶ PostgreSQL + pgvector   (desktop: SQLite)
Worker  ──▶ database health check, heartbeat, pg_dump backups
Trainer ──▶ separate Python process for LoRA training (GPU)
```

| Part | Stack |
| --- | --- |
| Frontend | Next.js 16, React 19, Tailwind CSS v4, Zod |
| Backend | FastAPI, SQLAlchemy, Alembic |
| Desktop | Electron + PyInstaller backend + Next.js standalone server |
| Local AI | llama.cpp, whisper.cpp, Piper — pinned versions, verified by SHA-256 |
| Landing page | separate Next.js app with waitlist ([`landing/`](landing)) |

AI requests always follow the same route: built-in local model → your own local server → online provider → deterministic fallback.

## Development

```bash
cd backend  && python -m pytest
cd frontend && npm test && npm run lint && npm run typecheck
cd desktop  && npm run check
```

Build the desktop installer yourself (Node 22 and Python 3.12; each OS builds its own):

```bash
cd frontend && npm ci
cd ../desktop && npm ci
npm run dist          # output in desktop/dist/
```

On Windows PowerShell use `npm.cmd` instead of `npm`.

| Doc | Content |
| --- | --- |
| [docs/funktionen.md](docs/funktionen.md) | every feature explained |
| [docs/api.md](docs/api.md) | API reference |
| [docs/entwicklung.md](docs/entwicklung.md) | tests, builds, CI |
| [docs/desktop.md](docs/desktop.md) | desktop app internals |
| [AGENTS.md](AGENTS.md) | pitfalls per area, for contributors |

The detailed docs, code comments and test names are in German.
