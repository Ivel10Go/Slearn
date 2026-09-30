"""Kleine Helfer fuer Umgebungsvariablen, die Worker und Backup-Runner teilen.

Diese Datei ist absichtlich winzig und enthaelt nichts als Standardbibliothek.
Sie ist aus zwei Gruenden aus `worker/main.py` herausgezogen:

* der Backup-Runner braucht dieselben Helfer (`utc_now`, `positive_float`,
  `required_value`, `normalize_database_url`), und ein Import von `worker.main`
  in `worker.backup` waere ein Zyklus, weil `main` den Runner startet;
* so bleibt sichtbar, dass der Worker ohne Pydantic und ohne FastAPI auskommt.

Grundregel fuer alles hier: jeder Fehler ist deutsch und nennt die Variable,
weil die Meldung im Worker-Log landet und dort ohne Kontext unbrauchbar waere.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def positive_float(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} muss eine Zahl sein, nicht {raw_value!r}") from error
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} muss groesser als null sein, ist aber {raw_value!r}")
    return value


def optional_positive_float(name: str, default: float) -> float:
    """Wie `positive_float`, aber ein leerer oder nicht gesetzter Wert ist erlaubt.

    Die Aufbewahrung von 0 Tagen ist eine eigene Bedeutung ("nichts aufheben")
    und darf deshalb nicht als Zahl geprueft werden - siehe `positive_int`.
    """
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    return positive_float(name, default)


def positive_int(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} muss eine ganze Zahl sein, nicht {raw_value!r}") from error
    if value < minimum:
        raise ValueError(f"{name} muss mindestens {minimum} sein, ist aber {value}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} darf hoechstens {maximum} sein, ist aber {value}")
    return value


def required_value(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} wird benoetigt")
    return value.strip()


def flag(name: str, default: bool) -> bool:
    """Liest `true`/`false`/`1`/`0`/`ja`/`nein` und lehnt alles andere ab."""
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "ja", "on", "yes"}:
        return True
    if normalized in {"0", "false", "nein", "off", "no"}:
        return False
    raise ValueError(f"{name} muss true oder false sein, ist aber {raw_value!r}")


def normalize_database_url(value: str) -> str:
    """Macht aus einer SQLAlchemy-URL eine URL, die psycopg und pg_dump verstehen."""
    normalized = value.strip()
    scheme, separator, remainder = normalized.partition("://")
    if not separator:
        return normalized
    if scheme == "postgres" or scheme.startswith("postgresql+"):
        scheme = "postgresql"
    return f"{scheme}://{remainder}"
