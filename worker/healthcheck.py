import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATUS_PATH = "/var/lib/school-assistant/worker-status/worker-status.json"


def is_healthy(status_path: Path, maximum_age_seconds: float, now: datetime | None = None) -> bool:
    if maximum_age_seconds <= 0:
        return False
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        heartbeat = datetime.fromisoformat(payload["last_heartbeat"])
        if heartbeat.tzinfo is None:
            return False
        current_time = now or datetime.now(timezone.utc)
        age_seconds = (current_time - heartbeat).total_seconds()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        payload.get("status") == "running"
        and payload.get("database") == "healthy"
        and -5 <= age_seconds <= maximum_age_seconds
    )


def main() -> int:
    status_path = Path(os.getenv("WORKER_STATUS_PATH", DEFAULT_STATUS_PATH))
    try:
        maximum_age_seconds = float(os.getenv("WORKER_HEALTH_MAX_AGE_SECONDS", "120"))
    except ValueError:
        return 1
    return 0 if is_healthy(status_path, maximum_age_seconds) else 1


if __name__ == "__main__":
    raise SystemExit(main())
