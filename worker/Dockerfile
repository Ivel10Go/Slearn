FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY worker/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --requirement /tmp/requirements.txt \
    && groupadd --gid 10001 worker \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin worker \
    && install -d -m 0750 -o 10001 -g 10001 /app/worker /var/lib/school-assistant/worker-status

COPY --chown=10001:10001 worker/main.py worker/healthcheck.py ./worker/

USER 10001:10001

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=4 \
    CMD ["python", "-m", "worker.healthcheck"]

CMD ["python", "-m", "worker.main"]
