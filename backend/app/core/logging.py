import json
import logging
import sys
from datetime import date, datetime, timezone
from typing import Any

_SENSITIVE_PARTS = ("password", "passwd", "secret", "token", "api_key", "authorization", "cookie")
_CONTENT_PARTS = ("content", "document", "body", "text", "description", "prompt")


def _safe_scalar(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return type(value).__name__


def _safe_fields(fields: dict[str, Any]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in fields.items():
        normalized = key.lower()
        if any(part in normalized for part in _SENSITIVE_PARTS):
            safe[key] = "[REDACTED]"
        elif any(part in normalized for part in _CONTENT_PARTS):
            safe[key] = "[OMITTED]"
        else:
            safe[key] = _safe_scalar(value)
    return safe


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", None)
        if not isinstance(event, str) or not event:
            event = record.name
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": event,
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload["fields"] = _safe_fields(fields)
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers.clear()
    root.addHandler(handler)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    logger.info(event, extra={"event": event, "fields": _safe_fields(fields)})
