FROM python:3.12-slim-bookworm

# `postgresql-client-15` liefert pg_dump und pg_restore. Die Version ist
# festgepinnt und zwar auf dieselbe wie der Server: das Image
# `pgvector/pgvector:0.8.1-pg15-bookworm` ist PostgreSQL 15, und ein Client
# derselben Hauptversion vermeidet beide Richtungen des Aergers (ein aelterer
# Client kann einen neueren Server nicht bedienen, ein neuerer Client kann
# alte Server Connect-Optionen nicht senden).
#
# Bewusst als Systembinary und nicht als Python-Paket: pg_dump ist ein
# externes Programm. Es ist der einzige Grund, warum dieses Image ueber
# `python:3.12-slim` hinaus ein Debian-Paket mitbringt - der Rest des Workers
# bleibt Standardbibliothek plus psycopg. Fuer eine Desktop-Installation wird
# dasselbe Programm aus der mitgelieferten PostgreSQL-Distribution genommen und
# ueber BACKUP_PG_DUMP_BINARY benannt; am Worker-Code aendert sich nichts.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN set -eux; \
    apt-get update; \
    apt-get install --no-install-recommends --yes postgresql-client-15; \
    rm -rf /var/lib/apt/lists/*

COPY worker/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --requirement /tmp/requirements.txt \
    && groupadd --gid 10001 worker \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin worker \
    && install -d -m 0750 -o 10001 -g 10001 /app/worker /var/lib/school-assistant/worker-status

# worker/tests/ wird bewusst nicht kopiert: die Tests laufen auf dem Host
# (`python -m unittest discover -s worker/tests` aus dem Repo-Root) und sollen
# nicht als Bestandteil des Laufzeitimages mitgepflegt werden.
COPY --chown=10001:10001 worker/main.py worker/healthcheck.py worker/env.py worker/backup.py ./worker/

USER 10001:10001

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=4 \
    CMD ["python", "-m", "worker.healthcheck"]

CMD ["python", "-m", "worker.main"]
