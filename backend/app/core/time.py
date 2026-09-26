from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return ensure_aware(value).astimezone(timezone.utc)


def zone_or_utc(name: str) -> ZoneInfo:
    """Liefert die Zeitzone des Namens und fällt bei unbekannten Zonen auf UTC zurück."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def resolve_timezone_name(name: str | None, fallback: str) -> str:
    """Prüft, ob ``name`` überhaupt eine Zone ist, und behält sonst ``fallback``."""
    candidate = name or fallback
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        return fallback
    return candidate


def day_window(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """Rechnet den UTC-Bereich eines ganzen Tages in der Ortszeit ``zone`` aus.

    Das Fenster ist links geschlossen und rechts offen: ``[Mitternacht, Mitternacht + 1 Tag)``.
    """
    start = as_utc(datetime.combine(day, time.min, tzinfo=zone))
    end = as_utc(datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone))
    return start, end
