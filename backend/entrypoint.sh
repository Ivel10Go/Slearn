#!/bin/sh
# Einziger Migrations-Start des Backends: Dockerfile-CMD und Compose führen
# beide dieses Skript aus, eine zweite Definition gibt es nicht mehr.
set -eu

# alembic.ini liegt neben diesem Skript. Ohne eigenes cd hinge der Start am
# Arbeitsverzeichnis des Aufrufers, im Image ist das /app.
cd "$(dirname "$0")"

# set -e lässt Uvicorn bewusst nicht starten, wenn das Upgrade fehlschlägt:
# ein Backend gegen ein nicht migriertes Schema ist schlimmer als kein Backend.
alembic -c alembic.ini upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
