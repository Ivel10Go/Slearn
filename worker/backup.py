"""Sichert die Datenbank nach einem Zeitplan und rotiert die Ablage nach Tagen.

Der Runner liegt im Worker und nicht im Backend, weil der Worker der einzige
Dauerlaeufer ist, der nichts vom HTTP-Prozess abhaengt. Das Backend ist per
Default ein einzelner Uvicorn-Prozess; ein Thread darin haenge an genau der
Sache, die der WebUntis-Scheduler im Backend zeigt.

`pg_dump` ist ein externes Programm und kein Python-Paket. Der Worker liefert es
nicht mit: im Image kommt es als Systembinary dazu (siehe `worker/Dockerfile`),
in einer Desktop-Installation aus der mitgelieferten PostgreSQL-Distribution.
Beides laeuft ueber dasselbe `pg_dump` auf dem PATH, damit der Code nicht davon
abhaengt, in welchem Container oder auf welchem Betriebssystem er laeuft.

Aus demselben Grund haengt der Ablageort an ``BACKUP_STORAGE_PATH`` und der
Datenbankhost an ``DATABASE_URL``: nichts davon ist hier fest verdrahtet. Der
Host wird nie geraten und nie auf ``localhost`` gesetzt, sondern aus der URL
gelesen, die Compose auf ``postgres`` zeigt - so steht im Worker-Log, welcher
Host tatsaechlich benutzt wurde.

Der Zeitplan ist ein fuenfteiliger Cron-Ausdruck ausgewertet **hier** und nicht
etwa vom Host-Cron: damit ist die Deutung unabhaengig von Betriebssystem und
Daemon. Unterstuetzt wird bewusst nur eine kleine, eindeutige Teilmenge
(``*``, ``N``, ``N-M``, ``N-M/S``, ``*/S`` und Kommalisten). Alles andere wird
beim Start abgelehnt, statt stillschweigend falsch interpretiert zu werden.

Die Kommentare und Meldungen in diesem Paket schreiben Umlaute als ae/oe/ue/ss.
Das ist Absicht: der Worker schreibt in ein Log ohne gesetzte Locale, und ein
Byte, das dort falsch kodiert wird, ist in ``docker compose logs`` nicht mehr zu
lesen. Der Status-JSON ist davon nicht betroffen - ``json.dumps`` schreibt ASCII
und die Oberflaeche dekodiert wieder.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from worker.env import (
    flag,
    normalize_database_url,
    optional_positive_float,
    positive_float,
    positive_int,
    utc_now,
)

LOGGER = logging.getLogger("school-assistant.worker.backup")

DEFAULT_SCHEDULE: Final[str] = "0 2 * * *"
DEFAULT_RETENTION_DAYS: Final[int] = 30
DEFAULT_DIRECTORY: Final[str] = "/var/lib/school-assistant/backups"
DEFAULT_FILE_PREFIX: Final[str] = "school"
DEFAULT_CATCHUP_HOURS: Final[float] = 72.0
DEFAULT_TIMEOUT_SECONDS: Final[float] = 1800.0
DEFAULT_CONNECT_TIMEOUT_SECONDS: Final[int] = 10
DEFAULT_PG_DUMP_BINARY: Final[str] = "pg_dump"

#: Zeitstempel im Dateinamen: UTC, lexikografisch sortierbar, ohne Trennzeichen
#: in der Datumsangabe. Genau dieses Muster erkennt die Rotation wieder - alles
#: andere im Verzeichnis wird nicht angefasst.
_TIMESTAMP_FORMAT: Final[str] = "%Y%m%dT%H%M%SZ"

#: Grosse von ``pg_dump``: eine Datenbank einer Schule passt bequem in einen
#: Block, aber ein 1-MiB-Block haelt den Speicherbedarf klein, falls der Host
#: einmal knapp wird.
_COPY_CHUNK_SIZE: Final[int] = 1024 * 1024


class BackupError(RuntimeError):
    """Ein Backup-Lauf ist fehlgeschlagen. Die Meldung ist deutsch und geht in den Status."""


class BackupConfigurationError(BackupError):
    """Die Konfiguration ist unbrauchbar; der Runner bleibt aus."""


@dataclass(frozen=True, slots=True)
class _CronField:
    name: str
    minimum: int
    maximum: int


_CRON_FIELDS: Final[tuple[_CronField, ...]] = (
    _CronField("Minute", 0, 59),
    _CronField("Stunde", 0, 23),
    _CronField("Tag des Monats", 1, 31),
    _CronField("Monat", 1, 12),
    _CronField("Wochentag", 0, 7),
)


@dataclass(frozen=True, slots=True)
class Schedule:
    """Ein fuenfteiliger Cron-Ausdruck in seiner unterstuetzten Teilmenge.

    ``None`` bedeutet ``*`` (Feld nicht eingeschraenkt). Der Wochentag zaehlt wie
    bei Cron ab Sonntag als 0; die 7 wird auf 0 abgebildet.
    """

    text: str
    minute: frozenset[int] | None
    hour: frozenset[int] | None
    day_of_month: frozenset[int] | None
    month: frozenset[int] | None
    weekday: frozenset[int] | None

    def matches(self, moment: datetime) -> bool:
        """Trifft der Ausdruck eine **naive** Ortszeit?

        Fuer ``Tag des Monats`` und ``Wochentag`` gilt die POSIX-Regel: sind
        beide Felder eingeschraenkt, genuegt eines von beiden. Deshalb ist
        ``0 2 * * 1-5`` (Werktage) nicht unmoeglich, was bei einer UND-Deutung
        der Fall waere.
        """
        if self.month is not None and moment.month not in self.month:
            return False
        if self.hour is not None and moment.hour not in self.hour:
            return False
        if self.minute is not None and moment.minute not in self.minute:
            return False
        day_of_month_restricted = self.day_of_month is not None
        weekday_restricted = self.weekday is not None
        if day_of_month_restricted and weekday_restricted:
            day_matches = moment.day in (self.day_of_month or frozenset())
            weekday = (moment.weekday() + 1) % 7
            return day_matches or weekday in (self.weekday or frozenset())
        if day_of_month_restricted and moment.day not in (self.day_of_month or frozenset()):
            return False
        if weekday_restricted and (moment.weekday() + 1) % 7 not in (self.weekday or frozenset()):
            return False
        return True


def _field_number(raw_value: str, field: _CronField) -> int:
    try:
        value = int(raw_value)
    except ValueError as error:
        raise BackupConfigurationError(
            f"Im Zeitplan ist {field.name} {raw_value!r}; erwartet wird eine Zahl."
        ) from error
    if value < field.minimum or value > field.maximum:
        raise BackupConfigurationError(
            f"Im Zeitplan liegt {field.name}={value} ausserhalb von {field.minimum}-{field.maximum}."
        )
    return value


def _field_step(raw_value: str, field: _CronField) -> int:
    try:
        step = int(raw_value)
    except ValueError as error:
        raise BackupConfigurationError(
            f"Im Zeitplan ist der Schrittweitenwert {raw_value!r} von {field.name} keine Zahl."
        ) from error
    if step <= 0:
        raise BackupConfigurationError(
            f"Im Zeitplan muss die Schrittweite von {field.name} groesser als null sein."
        )
    return step


def _parse_cron_part(raw_part: str, field: _CronField) -> set[int]:
    part = raw_part.strip()
    if not part:
        raise BackupConfigurationError(f"Im Zeitplan ist ein leeres Element in {field.name}.")
    base, separator, raw_step = part.partition("/")
    step = _field_step(raw_step, field) if separator else 1
    if base == "*":
        start, end = field.minimum, field.maximum
    elif "-" in base:
        raw_start, _, raw_end = base.partition("-")
        start = _field_number(raw_start, field)
        end = _field_number(raw_end, field)
        if start > end:
            raise BackupConfigurationError(
                f"Im Zeitplan ist der Bereich {part!r} von {field.name} umgekehrt."
            )
    else:
        start = end = _field_number(base, field)
    values = set(range(start, end + 1, step))
    if not values:
        raise BackupConfigurationError(
            f"Im Zeitplan ergibt {part!r} von {field.name} keinen einzigen Wert."
        )
    if field is _CRON_FIELDS[4]:
        values = {0 if value == 7 else value for value in values}
    return values


def _parse_cron_field(text: str, field: _CronField) -> frozenset[int] | None:
    normalized = text.strip()
    if normalized == "*":
        return None
    values: set[int] = set()
    for part in normalized.split(","):
        values |= _parse_cron_part(part, field)
    return frozenset(values)


def parse_schedule(text: str) -> Schedule:
    """Liest einen fuenfteiligen Cron-Ausdruck oder lehnt ihn mit Klartext ab.

    Bewusst nicht unterstuetzt und deshalb ein Fehler statt einer Deutung:
    ``@daily``-Kurzformen, Monats- und Wochentagsnamen, ``?`` und ``L``.
    """
    normalized = " ".join(text.split())
    parts = normalized.split(" ")
    if len(parts) != len(_CRON_FIELDS):
        raise BackupConfigurationError(
            f"BACKUP_SCHEDULE muss genau fuenf durch Leerzeichen getrennte Felder haben "
            f"(Minute Stunde Tag-des-Monats Monat Wochentag), hat aber {len(parts)}: {text!r}"
        )
    values = [_parse_cron_field(part, field) for part, field in zip(parts, _CRON_FIELDS, strict=True)]
    return Schedule(
        text=normalized,
        minute=values[0],
        hour=values[1],
        day_of_month=values[2],
        month=values[3],
        weekday=values[4],
    )


def previous_fire_time(
    now: datetime,
    schedule: Schedule,
    zone: ZoneInfo,
    *,
    lookback_hours: float = DEFAULT_CATCHUP_HOURS,
) -> datetime | None:
    """Der juengste faellige Zeitpunkt in der Zone ``zone``, als UTC-Zeitpunkt.

    Gesucht wird rueckwaerts in der **Ortszeit** und erst am Ende in UTC
    umgerechnet. Deshalb trifft ein ``0 2 * * *`` immer die richtige Ortszeit,
    auch wenn zwischen den letzten Tagen die Zeitumstellung lag. ``None`` heisst:
    im Suchfenster liegt kein Termin - bei einem exotischen Zeitplan wie
    ``0 2 1 1 *`` kann das der Normalfall sein.
    """
    start = now.astimezone(zone).replace(second=0, microsecond=0, tzinfo=None)
    limit = int(max(0.0, lookback_hours) * 60)
    candidate = start
    for _ in range(limit + 1):
        if schedule.matches(candidate):
            return candidate.replace(tzinfo=zone).astimezone(timezone.utc)
        candidate -= timedelta(minutes=1)
    return None


def next_fire_time(
    now: datetime,
    schedule: Schedule,
    zone: ZoneInfo,
    *,
    lookahead_hours: float = DEFAULT_CATCHUP_HOURS,
) -> datetime | None:
    """Der naechste Termin **nach** ``now``, als UTC-Zeitpunkt.

    Die Suche beginnt eine Minute nach ``now``, damit der gerade laufende Termin
    nicht als "naechster" zurueckgemeldet wird.
    """
    start = now.astimezone(zone).replace(second=0, microsecond=0, tzinfo=None) + timedelta(minutes=1)
    limit = int(max(1.0, lookahead_hours) * 60)
    candidate = start
    for _ in range(limit):
        if schedule.matches(candidate):
            return candidate.replace(tzinfo=zone).astimezone(timezone.utc)
        candidate += timedelta(minutes=1)
    return None


@dataclass(frozen=True, slots=True)
class DatabaseTarget:
    """Die Verbindungsangaben fuer ``pg_dump``, ohne Passwort im Aufruf."""

    host: str
    port: str
    database: str
    user: str
    password: str

    def describe(self) -> str:
        # Bewusst ohne Passwort: diese Zeile landet im Log und im Status.
        where = self.host or "lokaler Socket"
        return f"{where}:{self.port}/{self.database} als {self.user}"


def database_target(database_url: str) -> DatabaseTarget:
    """Zerlegt eine Verbindungs-URL in die Einzelangaben von ``pg_dump``.

    Der Host kommt aus der URL und wird nirgends geraten. Im Compose-Netz ist das
    ``postgres``; ``localhost`` entsteht nur, wenn jemand genau das in seine
    ``DATABASE_URL`` geschrieben hat - und steht dann im Log.
    """
    parts = urlsplit(normalize_database_url(database_url))
    database = unquote(parts.path.lstrip("/"))
    if not database:
        raise BackupConfigurationError("DATABASE_URL enthaelt keinen Datenbanknamen.")
    if not parts.username:
        raise BackupConfigurationError("DATABASE_URL enthaelt keinen Benutzernamen fuer pg_dump.")
    return DatabaseTarget(
        host=unquote(parts.hostname or ""),
        port=str(parts.port or 5432),
        database=database,
        user=unquote(parts.username),
        password=unquote(parts.password or ""),
    )


def resolve_pg_dump(binary: str = DEFAULT_PG_DUMP_BINARY) -> str:
    """Findet ``pg_dump``: Pfad aus der Konfiguration, dann PATH, dann Linux-Pakete.

    Die dritte Quelle deckt Debian ab, wo ``postgresql-client`` die Binary unter
    ``/usr/lib/postgresql/<version>/bin/`` ablegt und nur einen Wrapper in
    ``/usr/bin`` anbietet. Ohne Treffer ist das ein Konfigurationsfehler, kein
    Fehlschlag des Backups - der Worker bleibt dabei gesund.
    """
    candidates: list[str] = []
    configured = os.getenv("BACKUP_PG_DUMP_BINARY", "").strip()
    if configured:
        candidates.append(configured)
    located = shutil.which(binary)
    if located:
        candidates.append(located)
    candidates.extend(str(path) for path in sorted(Path("/usr/lib/postgresql").glob("*/bin/pg_dump"), reverse=True))
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    raise BackupConfigurationError(
        "pg_dump wurde nicht gefunden. Im Worker-Image kommt es als Paket postgresql-client dazu; "
        "sonst zeigt BACKUP_PG_DUMP_BINARY auf das Programm (unter Windows z. B. auf die "
        "pg_dump.exe der mitgelieferten PostgreSQL-Installation)."
    )


@dataclass(frozen=True, slots=True)
class BackupConfig:
    enabled: bool
    schedule: Schedule
    schedule_text: str
    zone: ZoneInfo
    zone_name: str
    retention_days: int
    directory: Path
    file_prefix: str
    timeout_seconds: float
    connect_timeout_seconds: int
    catchup_hours: float
    database: DatabaseTarget
    pg_dump: str


@dataclass(frozen=True, slots=True)
class BackupResult:
    ok: bool
    started_at: datetime
    finished_at: datetime
    file_name: str | None
    size_bytes: int | None
    kept_files: int
    removed_files: tuple[str, ...]
    error: str | None

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


def backup_file_name(prefix: str, moment: datetime) -> str:
    return f"{prefix}-{moment.astimezone(timezone.utc).strftime(_TIMESTAMP_FORMAT)}.dump"


def _dump_pattern(prefix: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(prefix)}-(\d{{8}})T(\d{{6}})Z\.dump$")


def _parse_dump_stamp(match: re.Match[str]) -> datetime | None:
    try:
        return datetime.strptime(f"{match.group(1)}{match.group(2)}", "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _dump_entries(config: BackupConfig) -> list[tuple[datetime, Path]]:
    """Alle Dumps, die genau unser Namensmuster treffen, neueste zuerst.

    Dateien mit anderem Namen werden bewusst ignoriert: eine von Hand abgelegte
    Sicherung darf weder rotiert noch als „letzter Lauf" gelesen werden.
    """
    pattern = _dump_pattern(config.file_prefix)
    entries: list[tuple[datetime, Path]] = []
    try:
        children = list(config.directory.iterdir())
    except OSError as error:
        LOGGER.error("Das Backup-Verzeichnis ist nicht lesbar: %s", error.strerror or type(error).__name__)
        return entries
    for path in children:
        match = pattern.match(path.name)
        if match is None:
            continue
        stamp = _parse_dump_stamp(match)
        if stamp is None or not path.is_file():
            continue
        entries.append((stamp, path))
    entries.sort(key=lambda entry: entry[0], reverse=True)
    return entries


def newest_backup_at(config: BackupConfig) -> datetime | None:
    """Der Zeitpunkt des neuesten Dumps, oder ``None``.

    Daraus leitet der Runner nach einem Neustart seinen letzten erfolgreichen Lauf
    ab. Eine eigene Zustandsdatei waere eine zweite Wahrheit, die auseinanderlaufen
    kann - der Dateiname ist die, die es ohnehin schon gibt.
    """
    entries = _dump_entries(config)
    return entries[0][0] if entries else None


def rotate_backups(config: BackupConfig, *, now: datetime) -> tuple[int, tuple[str, ...]]:
    """Loescht Dumps, die aelter als ``retention_days`` sind, und raeumt danach auf.

    Zwei Regeln, beide aus derselben Absicht: die Rotation zaehlt **Tage**, nicht
    Stueck, und sie loescht nie das einzige vorhandene Backup. Das neueste bleibt
    deshalb immer stehen, auch wenn es schon aelter als die Frist ist - sonst
    waere ein laengerer Ausfall genau der Fall, in dem am Ende nichts mehr da ist.
    """
    entries = _dump_entries(config)
    cutoff = now.astimezone(timezone.utc) - timedelta(days=config.retention_days)
    removed: list[str] = []
    for index, (stamp, path) in enumerate(entries):
        if index == 0:
            continue
        if stamp >= cutoff:
            continue
        try:
            path.unlink(missing_ok=True)
            path.with_name(f"{path.name}.sha256").unlink(missing_ok=True)
        except OSError as error:
            LOGGER.warning(
                "Ein altes Backup liess sich nicht loeschen: %s (%s)",
                path.name,
                error.strerror or type(error).__name__,
            )
            continue
        removed.append(path.name)
    removed.extend(_remove_orphan_checksums(config, cutoff=cutoff))
    kept = len(entries) - len([name for name in removed if name.endswith(".dump")])
    if removed:
        LOGGER.info(
            "%d aeltere Sicherungen geloescht, %d bleiben erhalten.",
            len([name for name in removed if name.endswith(".dump")]),
            kept,
        )
    return kept, tuple(removed)


def _remove_orphan_checksums(config: BackupConfig, *, cutoff: datetime) -> list[str]:
    """Pruefsummen ohne zugehoerigen Dump entfernen, sofern sie nicht aktuell sind."""
    removed: list[str] = []
    pattern = re.compile(rf"^{re.escape(config.file_prefix)}-(\d{{8}})T(\d{{6}})Z\.dump\.sha256$")
    try:
        candidates = list(config.directory.iterdir())
    except OSError:
        return removed
    for path in candidates:
        match = pattern.match(path.name)
        if match is None or not path.is_file():
            continue
        if path.with_suffix("").exists():
            continue
        stamp = _parse_dump_stamp(match)
        if stamp is None or stamp >= cutoff:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            LOGGER.warning(
                "Eine verwaiste Pruefsumme liess sich nicht loeschen: %s",
                error.strerror or type(error).__name__,
            )
            continue
        removed.append(path.name)
    return removed


def _directory_is_writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(directory, os.W_OK)


def _pg_dump_environment(config: BackupConfig) -> dict[str, str]:
    """Umgebung fuer ``pg_dump`` mit Passwort in ``PGPASSWORD``.

    Das Passwort gehoert bewusst nicht in die Argumentliste: die ist innerhalb
    des Containers ueber ``/proc`` und in Fehlermeldungen sichtbar.
    """
    environment = dict(os.environ)
    target = config.database
    environment["PGHOST"] = target.host
    environment["PGPORT"] = target.port
    environment["PGDATABASE"] = target.database
    environment["PGUSER"] = target.user
    environment["PGCONNECT_TIMEOUT"] = str(config.connect_timeout_seconds)
    environment["PGCLIENTENCODING"] = "UTF8"
    if target.password:
        environment["PGPASSWORD"] = target.password
    else:
        environment.pop("PGPASSWORD", None)
    return environment


def _pg_dump_command(config: BackupConfig) -> list[str]:
    """``pg_dump`` schreibt nach stdout; die Datei schreibt der Worker selbst.

    ``--no-owner`` und ``--no-privileges`` sind keine Kosmetik: die Restore-Uebung
    in docs/server.md spielt in eine Wegwerf-Datenbank mit einem anderen Benutzer
    zurueck, und ohne diese Schluessel wuerde ``pg_restore`` an den
    Eigentuemer-Anweisungen scheitern.
    """
    return [
        config.pg_dump,
        "--format=custom",
        "--compress=6",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        config.database.database,
    ]


def _last_error_line(stderr: bytes) -> str:
    for raw_line in reversed(stderr.decode("utf-8", errors="replace").splitlines()):
        line = raw_line.strip()
        if line:
            return line[:200]
    return "ohne Meldung auf der Fehlerausgabe"


def _run_pg_dump(config: BackupConfig, destination: Path) -> tuple[int, str]:
    """Startet ``pg_dump`` und schreibt stdout in ``destination``.

    stderr wird in einem eigenen Thread gelesen, weil es sonst den Pipe-Puffer
    fuellt und den Kindprozess blockiert, während der Hauptthread stdout liest.
    """
    # Fester Pfad aus der Konfiguration, kein Aufruf ueber eine Shell: weder
    # `sh -c` noch eine vom Nutzer gebaute Befehlszeile kommen hier hinein.
    process = subprocess.Popen(
        _pg_dump_command(config),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_pg_dump_environment(config),
        stdin=subprocess.DEVNULL,
    )
    collected: list[bytes] = []

    def _collect_stderr() -> None:
        assert process.stderr is not None
        try:
            collected.append(process.stderr.read())
        except OSError:
            collected.append(b"")

    reader = threading.Thread(target=_collect_stderr, name="backup-pg-dump-stderr", daemon=True)
    reader.start()
    watchdog = threading.Timer(config.timeout_seconds, process.kill)
    watchdog.daemon = True
    watchdog.start()
    digest = hashlib.sha256()
    try:
        with destination.open("wb") as handle:
            assert process.stdout is not None
            for chunk in iter(lambda: process.stdout.read(_COPY_CHUNK_SIZE), b""):
                handle.write(chunk)
                digest.update(chunk)
    except OSError as error:
        process.kill()
        process.wait(timeout=30)
        _close_pipe(process.stdout)
        _close_pipe(process.stderr)
        raise BackupError(
            f"Die Sicherung konnte nicht geschrieben werden: {error.strerror or type(error).__name__}"
        ) from error
    finally:
        watchdog.cancel()
    return_code = process.wait()
    reader.join(timeout=10)
    stderr = collected[0] if collected else b""
    _close_pipe(process.stdout)
    _close_pipe(process.stderr)
    if return_code != 0:
        raise BackupError(f"pg_dump endete mit Code {return_code}: {_last_error_line(stderr)}")
    return destination.stat().st_size, digest.hexdigest()


def _close_pipe(pipe: object) -> None:
    """Schliesst eine der beiden Pipes; ein offenes Handle erzeugt nur Warnungen."""
    closer = getattr(pipe, "close", None)
    if callable(closer):
        closer()


def run_backup(config: BackupConfig, *, now: datetime | None = None) -> BackupResult:
    """Ein vollstaendiger Lauf: Dump schreiben, pruefsummen, alte Bestände loeschen.

    Die Rotation laeuft nur nach einem **erfolgreichen** Dump. Ein fehlgeschlagener
    Lauf darf keine Dateien loeschen - sonst rueumt ein Ausfall die Historie weg.
    """
    started_at = now or utc_now()
    file_name = backup_file_name(config.file_prefix, started_at)
    final_path = config.directory / file_name
    partial_path = config.directory / f".{file_name}.part"
    try:
        config.directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return _failed_backup(
            started_at,
            f"Das Backup-Verzeichnis liess sich nicht anlegen: {error.strerror or type(error).__name__}",
        )
    kept_before = len(_dump_entries(config))
    try:
        size_bytes, checksum = _run_pg_dump(config, partial_path)
        partial_path.replace(final_path)
        checksum_path = config.directory / f"{file_name}.sha256"
        checksum_path.write_text(f"{checksum}  {file_name}\n", encoding="utf-8")
    except BackupError as error:
        partial_path.unlink(missing_ok=True)
        return _failed_backup(started_at, str(error), kept_files=kept_before)
    except OSError as error:
        partial_path.unlink(missing_ok=True)
        return _failed_backup(
            started_at,
            f"Die Sicherung liess sich nicht abschliessen: {error.strerror or type(error).__name__}",
            kept_files=kept_before,
        )
    kept_files, removed_files = rotate_backups(config, now=utc_now())
    return BackupResult(
        ok=True,
        started_at=started_at,
        finished_at=utc_now(),
        file_name=file_name,
        size_bytes=size_bytes,
        kept_files=kept_files,
        removed_files=removed_files,
        error=None,
    )


def _failed_backup(started_at: datetime, message: str, *, kept_files: int = 0) -> BackupResult:
    LOGGER.error("Backup fehlgeschlagen: %s", message)
    return BackupResult(
        ok=False,
        started_at=started_at,
        finished_at=utc_now(),
        file_name=None,
        size_bytes=None,
        kept_files=kept_files,
        removed_files=(),
        error=message,
    )


def disabled_backup_status(reason: str) -> dict[str, object]:
    """Der Statusblock, wenn der Runner nicht laeuft.

    Die Felder sind dieselben wie im laufenden Betrieb und tragen ``None``, damit
    ein Leser nicht zwischen „aus" und „kaputt" raten muss.
    """
    return {
        "enabled": False,
        "schedule": None,
        "timezone": None,
        "retention_days": None,
        "directory_writable": None,
        "pg_dump_path": None,
        "runner_available": False,
        "last_started_at": None,
        "last_finished_at": None,
        "last_successful_at": None,
        "next_due_at": None,
        "last_duration_seconds": None,
        "last_file": None,
        "last_size_bytes": None,
        "kept_files": None,
        "removed_files": [],
        "last_error": reason,
    }


class BackupRunner:
    """Haelt Konfiguration, letzten erfolgreichen Lauf und faellige Termine.

    Die Klasse kennt weder Threads noch Statusdatei. Sie beantwortet nur
    „faellig?" und „lauf jetzt" - der Thread in ``worker/main.py`` entscheidet,
    wann er fragt, und schreibt danach das Ergebnis in die Statusdatei.
    """

    def __init__(self, config: BackupConfig, *, now: datetime | None = None) -> None:
        self.config = config
        moment = now or utc_now()
        self._last_successful_at = newest_backup_at(config)
        self._last_fire_at: datetime | None = None
        self._next_fire_at: datetime | None = None
        self._last_result: BackupResult | None = None
        self._kept_files = len(_dump_entries(config))
        self._directory_writable = _directory_is_writable(config.directory)
        self._refresh_anchors(moment)
        LOGGER.info(
            "Backup-Runner bereit: Zeitplan %s (%s), Aufbewahrung %d Tage, Ablage %s",
            config.schedule_text,
            config.zone_name,
            config.retention_days,
            config.directory,
        )
        LOGGER.info("Backup-Ziel: %s", config.database.describe())
        LOGGER.info("pg_dump: %s", config.pg_dump)
        if self._last_successful_at is None:
            LOGGER.info("Noch keine Sicherung im Verzeichnis gefunden; es wird nachgeholt.")
        else:
            LOGGER.info("Letzte gefundene Sicherung: %s", self._last_successful_at.isoformat())
        if not self._directory_writable:
            LOGGER.error(
                "%s ist nicht beschreibbar. Im Worker muss das Backup-Verzeichnis read/write "
                "eingebunden sein, sonst schlaegt jeder Lauf fehl.",
                config.directory,
            )

    @property
    def last_successful_at(self) -> datetime | None:
        return self._last_successful_at

    def _refresh_anchors(self, now: datetime) -> None:
        self._last_fire_at = previous_fire_time(
            now,
            self.config.schedule,
            self.config.zone,
            lookback_hours=self.config.catchup_hours,
        )
        self._next_fire_at = next_fire_time(
            now,
            self.config.schedule,
            self.config.zone,
            lookahead_hours=self.config.catchup_hours,
        )

    def is_due(self, now: datetime | None = None) -> bool:
        moment = now or utc_now()
        last = self._last_successful_at
        if last is not None and self._last_fire_at is not None and last >= self._last_fire_at:
            return False
        self._refresh_anchors(moment)
        if self._last_fire_at is None:
            return False
        return last is None or last < self._last_fire_at

    def run_once(self, now: datetime | None = None) -> BackupResult:
        result = run_backup(self.config, now=now)
        self._last_result = result
        if result.ok:
            self._last_successful_at = utc_now()
            LOGGER.info(
                "Backup geschrieben: %s (%d Byte, %.1f s), %d Sicherungen auf dem Datentraeger.",
                result.file_name,
                result.size_bytes or 0,
                result.duration_seconds,
                result.kept_files,
            )
        self._refresh_anchors(utc_now())
        return result

    def status(self, now: datetime | None = None, *, error: str | None = None) -> dict[str, object]:
        """Der Block fuer die Statusdatei.

        ``error`` ueberschreibt die gemeldete Fehlermeldung. Der Thread benutzt das
        fuer einen Fehler, der ausserhalb von `run_backup` aufgetreten ist; der
        Zeitplan bleibt dabei sichtbar, statt den ganzen Runner zu verbergen.
        """
        moment = now or utc_now()
        self._refresh_anchors(moment)
        entries = _dump_entries(self.config)
        self._kept_files = len(entries)
        result = self._last_result
        next_due: datetime | None = self._next_fire_at
        if self.is_due(moment):
            # Eine nachzuholende Sicherung ist faellig und wartet nicht bis zum
            # naechsten Termin - genau das ist der Unterschied zu ``next_fire_at``.
            next_due = moment
        return {
            "enabled": True,
            "schedule": self.config.schedule_text,
            "timezone": self.config.zone_name,
            "retention_days": self.config.retention_days,
            "directory_writable": self._directory_writable,
            "pg_dump_path": self.config.pg_dump,
            "runner_available": True,
            "last_started_at": result.started_at.isoformat() if result else None,
            "last_finished_at": result.finished_at.isoformat() if result else None,
            "last_successful_at": self._last_successful_at.isoformat() if self._last_successful_at else None,
            "next_due_at": next_due.isoformat() if next_due else None,
            "last_duration_seconds": round(result.duration_seconds, 3) if result else None,
            "last_file": result.file_name if result and result.ok else None,
            "last_size_bytes": result.size_bytes if result and result.ok else None,
            "kept_files": self._kept_files,
            "removed_files": list(result.removed_files) if result else [],
            "last_error": error or (result.error if result and not result.ok else None),
        }


def load_backup_config(database_url: str) -> BackupConfig:
    """Liest die Konfiguration aus der Umgebung.

    Wird absichtlich **nicht** tolerant gegenueber einer schlechten
    ``BACKUP_SCHEDULE``: der Aufrufer laesst den Runner dann aus, statt die
    Sicherung mit einer falsch verstandenen Zeitangabe zu betreiben.
    """
    if not flag("BACKUP_ENABLED", True):
        raise BackupConfigurationError("BACKUP_ENABLED ist false; der Runner bleibt aus.")
    schedule_text = os.getenv("BACKUP_SCHEDULE", DEFAULT_SCHEDULE).strip() or DEFAULT_SCHEDULE
    schedule = parse_schedule(schedule_text)
    zone_name = os.getenv("SCHOOL_TIMEZONE", "Europe/Berlin").strip() or "Europe/Berlin"
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise BackupConfigurationError(f"SCHOOL_TIMEZONE={zone_name!r} ist keine gueltige IANA-Zeitzone.") from error
    directory = Path(os.getenv("BACKUP_STORAGE_PATH", DEFAULT_DIRECTORY).strip() or DEFAULT_DIRECTORY)
    file_prefix = os.getenv("BACKUP_FILE_PREFIX", DEFAULT_FILE_PREFIX).strip() or DEFAULT_FILE_PREFIX
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,48}", file_prefix):
        raise BackupConfigurationError(
            "BACKUP_FILE_PREFIX darf nur Buchstaben, Ziffern, Punkt, Unterstrich und Bindestrich enthalten."
        )
    return BackupConfig(
        enabled=True,
        schedule=schedule,
        schedule_text=schedule.text,
        zone=zone,
        zone_name=zone_name,
        retention_days=positive_int("BACKUP_RETENTION_DAYS", DEFAULT_RETENTION_DAYS, minimum=0, maximum=3650),
        directory=directory,
        file_prefix=file_prefix,
        timeout_seconds=positive_float("BACKUP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        connect_timeout_seconds=positive_int(
            "BACKUP_CONNECT_TIMEOUT_SECONDS", DEFAULT_CONNECT_TIMEOUT_SECONDS, minimum=1, maximum=120
        ),
        catchup_hours=optional_positive_float("BACKUP_CATCHUP_HOURS", DEFAULT_CATCHUP_HOURS),
        database=database_target(database_url),
        pg_dump=resolve_pg_dump(),
    )


__all__ = [
    "BackupConfig",
    "BackupConfigurationError",
    "BackupError",
    "BackupResult",
    "BackupRunner",
    "Schedule",
    "backup_file_name",
    "database_target",
    "disabled_backup_status",
    "load_backup_config",
    "newest_backup_at",
    "next_fire_time",
    "parse_schedule",
    "previous_fire_time",
    "resolve_pg_dump",
    "rotate_backups",
    "run_backup",
]
