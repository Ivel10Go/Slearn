"""Startpunkt des Backends in der Desktop-App.

Die Desktop-App (``desktop/main.js``) startet diese Datei - eingefroren per
PyInstaller als ``lernraum-backend`` - mit allen Einstellungen als
Umgebungsvariablen. Hier passiert, was im Container ``entrypoint.sh`` tut:
erst die Migrationen, dann Uvicorn. Ein Backend gegen ein nicht migriertes
Schema ist schlimmer als keins, deshalb startet Uvicorn nur nach Erfolg.

Zusaetzlich beendet sich der Prozess, sobald die Desktop-App verschwindet:
Electron haelt ``stdin`` offen, und ein EOF heisst, dass der Elternprozess weg
ist - auch nach einem Absturz, bei dem niemand mehr ``kill`` schickt. Ohne
das bliebe ein verwaister Server zurueck, der den Port und die Datenbank haelt.
"""

import argparse
import os
import sys
import threading
from pathlib import Path


def migrations_dir() -> Path:
    # Eingefroren liegen die Migrationen unter `sys._MEIPASS/migrations`
    # (siehe `lernraum-backend.spec`), sonst als `alembic/` neben dieser Datei.
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root) / "migrations"
    return Path(__file__).resolve().parent / "alembic"


def run_migrations(database_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    # Ohne `alembic.ini`: die Datei setzt nur Pfade und Logging, und das
    # Logging der Ini wuerde Uvicorns Konfiguration ueberschreiben.
    config = Config()
    config.set_main_option("script_location", str(migrations_dir()))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def watch_parent() -> None:
    def wait_for_eof() -> None:
        try:
            while sys.stdin.read(1024):
                pass
        except (OSError, ValueError):
            pass
        os._exit(0)

    threading.Thread(target=wait_for_eof, name="parent-watch", daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Lernraum-Backend fuer die Desktop-App")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--watch-parent", action="store_true", help="beenden, sobald stdin geschlossen wird")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith("sqlite"):
        # Die Desktop-App kennt nur ihre eigene Datei. Ein vergessenes
        # DATABASE_URL aus einer Server-`.env` soll hier nicht still eine
        # fremde Datenbank migrieren.
        print("DATABASE_URL muss auf eine SQLite-Datei zeigen.", file=sys.stderr)
        sys.exit(2)

    if args.watch_parent:
        watch_parent()

    # In der Desktop-App holt und startet das Backend llama-server,
    # whisper-server und piper selbst (app/services/local_runtimes.py).
    os.environ.setdefault("LOCAL_RUNTIMES_ENABLED", "true")

    run_migrations(database_url)

    import uvicorn
    from app.main import app

    uvicorn.run(app, host=args.host, port=args.port, log_level=os.environ.get("LOG_LEVEL", "warning").lower(), access_log=False)


if __name__ == "__main__":
    main()
