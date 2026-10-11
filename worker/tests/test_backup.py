import hashlib
import json
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from worker.backup import (
    BackupConfig,
    BackupConfigurationError,
    BackupRunner,
    Schedule,
    _pg_dump_command,
    _pg_dump_environment,
    database_target,
    disabled_backup_status,
    next_fire_time,
    parse_schedule,
    previous_fire_time,
    resolve_pg_dump,
    rotate_backups,
    run_backup,
)
from worker.env import normalize_database_url

# Die Tests loesen absichtlich Fehler aus (fehlgeschlagene Laeufe, nicht
# beschreibbare Verzeichnisse). Ohne das schreibt das Logging nach stderr und
# die Ausgabe des Laufs ist unlesbar.
logging.disable(logging.CRITICAL)


def parse(text: str) -> Schedule:
    return parse_schedule(text)


class ScheduleParsingTests(unittest.TestCase):
    def test_accepts_the_documented_default(self) -> None:
        schedule = parse("0 2 * * *")
        self.assertEqual(schedule.text, "0 2 * * *")
        self.assertEqual(schedule.minute, frozenset({0}))
        self.assertEqual(schedule.hour, frozenset({2}))
        self.assertIsNone(schedule.day_of_month)
        self.assertIsNone(schedule.weekday)

    def test_accepts_steps_ranges_and_lists(self) -> None:
        self.assertEqual(parse("*/15 9-17 * * 1-5").minute, frozenset({0, 15, 30, 45}))
        self.assertEqual(parse("*/15 9-17 * * 1-5").hour, frozenset(range(9, 18)))
        self.assertEqual(parse("0,30 2,14 * * *").minute, frozenset({0, 30}))
        self.assertEqual(parse("0 0 1 */3 *").month, frozenset({1, 4, 7, 10}))
        self.assertEqual(parse("0 0 1 * 0-2/2").weekday, frozenset({0, 2}))

    def test_treats_seven_as_sunday(self) -> None:
        self.assertEqual(parse("0 3 * * 7").weekday, frozenset({0}))

    def test_keeps_extra_whitespace_but_normalizes_it(self) -> None:
        self.assertEqual(parse("  0   2  *  *  * ").text, "0 2 * * *")

    def test_rejects_unsupported_shorthand_and_named_fields(self) -> None:
        for text in ("@daily", "0 2 * * MON", "0 2 ? * *", "0 2 * * 1L", "0 2 * * MON-FRI"):
            with self.subTest(text=text):
                with self.assertRaises(BackupConfigurationError):
                    parse_schedule(text)

    def test_rejects_wrong_field_count_and_out_of_range_values(self) -> None:
        cases = (
            "0 2 * *",
            "0 2 * * * *",
            "60 2 * * *",
            "0 24 * * *",
            "0 2 0 * *",
            "0 2 * 13 *",
            "0 2 * * 8",
            "5-1 2 * * *",
        )
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(BackupConfigurationError):
                    parse_schedule(text)

    def test_rejects_non_numeric_step(self) -> None:
        with self.assertRaises(BackupConfigurationError):
            parse_schedule("*/x 2 * * *")
        with self.assertRaises(BackupConfigurationError):
            parse_schedule("*/0 2 * * *")

    def test_messages_are_german_and_name_the_field(self) -> None:
        with self.assertRaises(BackupConfigurationError) as context:
            parse_schedule("0 99 * * *")
        self.assertIn("Stunde", str(context.exception))


class FireTimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.zone = ZoneInfo("Europe/Berlin")

    def test_finds_the_daily_slot_in_local_time(self) -> None:
        schedule = parse("0 2 * * *")
        now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
        fire = previous_fire_time(now, schedule, self.zone)
        self.assertIsNotNone(fire)
        assert fire is not None
        # 02:00 Ortszeit im September ist 00:00 UTC (Sommerzeit).
        self.assertEqual(fire, datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(fire.astimezone(self.zone).hour, 2)

    def test_reads_the_slot_in_winter_time_too(self) -> None:
        schedule = parse("0 2 * * *")
        now = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
        fire = previous_fire_time(now, schedule, self.zone)
        assert fire is not None
        # 02:00 Ortszeit im Dezember ist 01:00 UTC.
        self.assertEqual(fire, datetime(2026, 12, 1, 1, 0, tzinfo=timezone.utc))

    def test_next_fire_is_strictly_after_the_current_minute(self) -> None:
        schedule = parse("0 2 * * *")
        exactly = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)
        upcoming = next_fire_time(exactly, schedule, self.zone)
        assert upcoming is not None
        self.assertGreater(upcoming, exactly)
        self.assertEqual(upcoming.astimezone(self.zone).strftime("%Y-%m-%d %H:%M"), "2026-10-01 02:00")

    def test_returns_none_when_the_window_has_no_slot(self) -> None:
        schedule = parse("0 2 1 1 *")
        now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
        self.assertIsNone(previous_fire_time(now, schedule, self.zone, lookback_hours=72))
        self.assertIsNone(next_fire_time(now, schedule, self.zone, lookahead_hours=72))

    def test_weekday_rule_uses_posix_or_semantics(self) -> None:
        # 1. Maerz 2026 ist ein Sonntag. Beide Felder sind eingeschraenkt, deshalb
        # greift die ODER-Regel: der Tag passt auch ohne Wochentag.
        schedule = parse("0 12 1 * 5")
        sunday = datetime(2026, 3, 1, 12, 0)
        self.assertTrue(schedule.matches(sunday))
        # 4. Maerz ist ein Mittwoch; weder Tag noch Wochentag passen.
        self.assertFalse(schedule.matches(datetime(2026, 3, 4, 12, 0)))
        # 6. Maerz ist ein Freitag: der Wochentag allein genuegt.
        self.assertTrue(schedule.matches(datetime(2026, 3, 6, 12, 0)))

    def test_single_asterisk_day_field_combines_with_weekday(self) -> None:
        schedule = parse("0 2 * * 1-5")
        self.assertTrue(schedule.matches(datetime(2026, 3, 6, 2, 0)))  # Freitag
        self.assertFalse(schedule.matches(datetime(2026, 3, 7, 2, 0)))  # Samstag


class DatabaseTargetTests(unittest.TestCase):
    def test_reads_host_and_port_from_the_url(self) -> None:
        target = database_target("postgresql+psycopg://schooluser:schoolpass@postgres:5432/school")
        self.assertEqual(target.host, "postgres")
        self.assertEqual(target.port, "5432")
        self.assertEqual(target.database, "school")
        self.assertEqual(target.user, "schooluser")
        self.assertEqual(target.password, "schoolpass")

    def test_keeps_whatever_host_the_url_names(self) -> None:
        # Bewusst kein Ersetzen durch localhost: im Compose-Netz ist der Host der
        # Service `postgres`, im Desktop-Betrieb der lokale Datenbankserver.
        target = database_target("postgresql://user:pw@127.0.0.1:5432/school")
        self.assertEqual(target.host, "127.0.0.1")
        self.assertEqual(database_target("postgresql://user@pg.example.org/school").host, "pg.example.org")

    def test_defaults_the_port_to_the_postgres_default(self) -> None:
        self.assertEqual(database_target("postgresql://user:pw@postgres/school").port, "5432")

    def test_allows_an_empty_host_for_a_local_socket(self) -> None:
        # Ohne Host benutzt pg_dump den lokalen Socket. Das ist eine gueltige
        # Konfiguration fuer eine Desktop-Installation, kein Fehler.
        target = database_target("postgresql://localuser:pw@/school")
        self.assertEqual(target.host, "")
        self.assertEqual(target.database, "school")
        self.assertEqual(target.user, "localuser")

    def test_rejects_urls_without_database_or_user(self) -> None:
        with self.assertRaises(BackupConfigurationError):
            database_target("postgresql://user:pw@postgres:5432/")
        with self.assertRaises(BackupConfigurationError):
            database_target("postgresql://postgres:5432/school")

    def test_describes_itself_without_the_password(self) -> None:
        target = database_target("postgresql://user:super-geheim@postgres:5432/school")
        self.assertNotIn("super-geheim", target.describe())
        self.assertIn("postgres:5432/school", target.describe())


class PgDumpResolutionTests(unittest.TestCase):
    def test_reports_a_missing_binary_in_german(self) -> None:
        # Ein Name, den es garantiert nicht gibt, muss sauber scheitern - auch auf
        # einem Rechner, auf dem unter /usr/lib/postgresql ein echtes pg_dump liegt.
        with tempfile.TemporaryDirectory() as leer, mock.patch.dict(os.environ, {"BACKUP_PG_DUMP_BINARY": ""}):
            with self.assertRaises(BackupConfigurationError) as context:
                resolve_pg_dump("pg_dump-die-es-nicht-gibt", package_root=Path(leer))
        self.assertIn("pg_dump", str(context.exception))
        self.assertIn("BACKUP_PG_DUMP_BINARY", str(context.exception))


class RotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.now = datetime(2026, 9, 30, 2, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write(self, name: str) -> Path:
        path = self.directory / name
        path.write_bytes(b"dump")
        return path

    def config(self, retention_days: int = 30):
        from worker.backup import BackupConfig

        return BackupConfig(
            enabled=True,
            schedule=parse("0 2 * * *"),
            schedule_text="0 2 * * *",
            zone=ZoneInfo("Europe/Berlin"),
            zone_name="Europe/Berlin",
            retention_days=retention_days,
            directory=self.directory,
            file_prefix="school",
            timeout_seconds=60.0,
            connect_timeout_seconds=10,
            catchup_hours=72.0,
            database=database_target("postgresql://u:p@postgres:5432/school"),
            pg_dump="pg_dump",
        )

    def test_counts_days_and_not_files(self) -> None:
        # 40 Tage alt, Frist ist 30 Tage: weg. 40 Minuten alt: bleibt.
        old = self.write("school-20260821T020000Z.dump")
        recent = self.write("school-20260930T012000Z.dump")
        kept, removed = rotate_backups(self.config(), now=self.now)
        self.assertEqual(kept, 1)
        self.assertIn(old.name, removed)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_never_deletes_the_only_backup(self) -> None:
        only = self.write("school-20240101T020000Z.dump")
        kept, removed = rotate_backups(self.config(retention_days=1), now=self.now)
        self.assertEqual(kept, 1)
        self.assertEqual(removed, ())
        self.assertTrue(only.exists())

    def test_keeps_the_newest_even_when_it_is_older_than_the_limit(self) -> None:
        newest = self.write("school-20260821T020000Z.dump")
        self.write("school-20260820T020000Z.dump")
        kept, removed = rotate_backups(self.config(retention_days=1), now=self.now)
        self.assertEqual(kept, 1)
        self.assertEqual(len(removed), 1)
        self.assertTrue(newest.exists())

    def test_zero_days_keeps_only_the_newest(self) -> None:
        newest = self.write("school-20260929T020000Z.dump")
        older = self.write("school-20260928T020000Z.dump")
        kept, removed = rotate_backups(self.config(retention_days=0), now=self.now)
        self.assertEqual(kept, 1)
        self.assertEqual(removed, (older.name,))
        self.assertTrue(newest.exists())

    def test_removes_the_checksum_beside_the_dump(self) -> None:
        old = self.write("school-20260821T020000Z.dump")
        checksum = self.directory / f"{old.name}.sha256"
        checksum.write_text("abc  x\n", encoding="utf-8")
        # Ein neueres Backup muss da sein, sonst waere das alte das einzige.
        self.write("school-20260930T010000Z.dump")
        rotate_backups(self.config(retention_days=1), now=self.now)
        self.assertFalse(old.exists())
        self.assertFalse(checksum.exists())

    def test_removes_an_orphaned_checksum_only_when_it_is_old(self) -> None:
        orphan = self.directory / "school-20240101T020000Z.dump.sha256"
        orphan.write_text("abc  x\n", encoding="utf-8")
        _, removed = rotate_backups(self.config(retention_days=30), now=self.now)
        self.assertIn(orphan.name, removed)
        self.assertFalse(orphan.exists())

    def test_leaves_foreign_files_untouched(self) -> None:
        foreign = self.write("manuell-sicher.dump")
        foreign_checksum = self.write("school-20240101T020000Z.dump.sha256")
        dump = self.write("school-20240101T020000Z.dump")
        rotate_backups(self.config(retention_days=1), now=self.now)
        self.assertTrue(foreign.exists())
        self.assertTrue(dump.exists())
        self.assertTrue(foreign_checksum.exists())

    def test_ignores_unparsable_timestamps_in_the_name(self) -> None:
        broken = self.write("school-20261332T999999Z.dump")
        rotate_backups(self.config(retention_days=1), now=self.now)
        self.assertTrue(broken.exists())


class DisabledStatusTests(unittest.TestCase):
    def test_always_carries_the_same_keys_as_a_running_runner(self) -> None:
        from worker.backup import BackupRunner, BackupConfig

        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            config = BackupConfig(
                enabled=True,
                schedule=parse("0 2 * * *"),
                schedule_text="0 2 * * *",
                zone=ZoneInfo("UTC"),
                zone_name="UTC",
                retention_days=30,
                directory=directory,
                file_prefix="school",
                timeout_seconds=60.0,
                connect_timeout_seconds=10,
                catchup_hours=72.0,
                database=database_target("postgresql://u:p@postgres:5432/school"),
                pg_dump="pg_dump",
            )
            running = BackupRunner(config, now=datetime(2026, 9, 30, 2, 0, tzinfo=timezone.utc)).status()
        disabled = disabled_backup_status("BACKUP_ENABLED ist false; der Runner bleibt aus.")
        self.assertEqual(set(running), set(disabled))
        self.assertFalse(disabled["enabled"])
        self.assertFalse(disabled["runner_available"])
        self.assertIsNone(disabled["last_successful_at"])
        self.assertIsNone(disabled["last_file"])


class StatusStrictnessTests(unittest.TestCase):
    """Der Healthcheck darf von den neuen Feldern unberuehrt bleiben."""

    def test_a_failed_backup_keeps_the_worker_healthy(self) -> None:
        from worker.healthcheck import is_healthy

        with tempfile.TemporaryDirectory() as name:
            status_path = Path(name) / "worker-status.json"
            moment = datetime(2026, 9, 30, 2, 0, tzinfo=timezone.utc)
            payload = {
                "status": "running",
                "database": "healthy",
                "last_heartbeat": moment.isoformat(),
                "last_error": None,
                "backup": {
                    "enabled": True,
                    "schedule": "0 2 * * *",
                    "last_error": "pg_dump endete mit Code 1: connection refused",
                    "last_successful_at": None,
                    "next_due_at": moment.isoformat(),
                },
            }
            status_path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertTrue(is_healthy(status_path, 120, moment))

    def test_backup_fields_are_timezone_aware(self) -> None:
        from worker.backup import BackupRunner, BackupConfig

        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            config = BackupConfig(
                enabled=True,
                schedule=parse("0 2 * * *"),
                schedule_text="0 2 * * *",
                zone=ZoneInfo("Europe/Berlin"),
                zone_name="Europe/Berlin",
                retention_days=30,
                directory=directory,
                file_prefix="school",
                timeout_seconds=60.0,
                connect_timeout_seconds=10,
                catchup_hours=72.0,
                database=database_target("postgresql://u:p@postgres:5432/school"),
                pg_dump="pg_dump",
            )
            runner = BackupRunner(config)
            for key in ("last_successful_at", "next_due_at", "last_started_at", "last_finished_at"):
                value = runner.status()[key]
                if value is None:
                    continue
                parsed = datetime.fromisoformat(str(value))
                self.assertIsNotNone(parsed.tzinfo, f"{key} muss zeitzonenbehaftet sein")
                self.assertEqual(parsed.utcoffset(), timedelta(0))


class UrlNormalizationTests(unittest.TestCase):
    def test_rewrites_sqlalchemy_schemes(self) -> None:
        self.assertEqual(normalize_database_url("postgresql+psycopg://a:b@c/d"), "postgresql://a:b@c/d")
        self.assertEqual(normalize_database_url("postgres://a:b@c/d"), "postgresql://a:b@c/d")
        self.assertEqual(normalize_database_url("postgresql://a:b@c/d"), "postgresql://a:b@c/d")


class BackupRunTests(unittest.TestCase):
    """Der Lauf gegen ein statt pg_dump eingesetztes Programm.

    Echte ``pg_dump``-Aufrufe brauchen eine Datenbank und das Binary aus dem
    Image. Ersetzt wird deshalb nur die Befehlszeile - der Rest des Ablaufs
    (Pipe, Pruefsumme, atomares Umbenennen, Rotation) laeuft wirklich.
    """

    DUMP_CONTENT = b"PGDMP-egal-ob-echt"

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def config(self, retention_days: int = 30) -> BackupConfig:
        return BackupConfig(
            enabled=True,
            schedule=parse("0 2 * * *"),
            schedule_text="0 2 * * *",
            zone=ZoneInfo("UTC"),
            zone_name="UTC",
            retention_days=retention_days,
            directory=self.directory,
            file_prefix="school",
            timeout_seconds=60.0,
            connect_timeout_seconds=10,
            catchup_hours=72.0,
            database=database_target("postgresql://u:p@postgres:5432/school"),
            pg_dump="pg_dump",
        )

    def fake_command(self, *, returncode: int = 0, stderr: str = "") -> list[str]:
        if returncode == 0:
            program = "import sys\nsys.stdout.buffer.write(%r)\n" % self.DUMP_CONTENT
        else:
            program = f"import sys\nsys.stderr.write({stderr!r})\nsys.exit({returncode})\n"
        return [sys.executable, "-c", program]

    def test_writes_the_dump_and_its_checksum(self) -> None:
        moment = datetime(2026, 9, 30, 2, 0, tzinfo=timezone.utc)
        with mock.patch("worker.backup._pg_dump_command", return_value=self.fake_command()):
            result = run_backup(self.config(), now=moment)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.file_name, "school-20260930T020000Z.dump")
        dump = self.directory / result.file_name
        self.assertEqual(dump.read_bytes(), self.DUMP_CONTENT)
        expected = hashlib.sha256(self.DUMP_CONTENT).hexdigest()
        checksum_text = (self.directory / f"{result.file_name}.sha256").read_text(encoding="utf-8")
        self.assertEqual(checksum_text, f"{expected}  {result.file_name}\n")
        self.assertEqual(result.kept_files, 1)
        self.assertEqual(result.removed_files, ())
        self.assertGreaterEqual(result.size_bytes, len(self.DUMP_CONTENT))

    def test_passes_the_flags_the_restore_drill_needs(self) -> None:
        arguments = _pg_dump_command(self.config())
        self.assertIn("--format=custom", arguments)
        self.assertIn("--no-owner", arguments)
        self.assertIn("--no-privileges", arguments)
        self.assertNotIn("-f", arguments, "ohne -f schreibt pg_dump nach stdout")
        # Der Datenbankname steht in der Argumentliste, das Passwort nicht.
        self.assertEqual(arguments[-1], "school")
        self.assertNotIn(":p@", " ".join(arguments))
        environment = _pg_dump_environment(self.config())
        self.assertEqual(environment["PGHOST"], "postgres")
        self.assertEqual(environment["PGPASSWORD"], "p")

    def test_reports_a_failure_without_leaving_a_partial_file(self) -> None:
        command = self.fake_command(returncode=1, stderr="pg_dump: error: connection refused\n")
        with mock.patch("worker.backup._pg_dump_command", return_value=command):
            result = run_backup(self.config())
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertIn("Code 1", result.error)
        self.assertIn("connection refused", result.error)
        self.assertIsNone(result.file_name)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_a_failed_run_does_not_rotate_anything_away(self) -> None:
        old = self.directory / "school-20250101T020000Z.dump"
        old.write_bytes(b"alt")
        recent = self.directory / "school-20260930T010000Z.dump"
        recent.write_bytes(b"neu")
        command = self.fake_command(returncode=2, stderr="pg_dump: error: no password supplied\n")
        with mock.patch("worker.backup._pg_dump_command", return_value=command):
            result = run_backup(self.config(retention_days=1))
        self.assertFalse(result.ok)
        self.assertTrue(old.exists())
        self.assertTrue(recent.exists())

    def test_the_runner_is_due_once_and_then_quiet(self) -> None:
        runner = BackupRunner(self.config())
        self.assertIsNone(runner.last_successful_at)
        self.assertTrue(runner.is_due(), "ohne jede Sicherung ist der letzte Termin nachzuholen")
        with mock.patch("worker.backup._pg_dump_command", return_value=self.fake_command()):
            result = runner.run_once()
        self.assertTrue(result.ok, result.error)
        self.assertIsNotNone(runner.last_successful_at)
        self.assertFalse(runner.is_due(), "nach dem Lauf ist der Termin abgearbeitet")

    def test_a_restart_recovers_the_last_run_from_the_file_name(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "school-20260929T020000Z.dump").write_bytes(b"gestern")
        moment = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
        runner = BackupRunner(self.config(), now=moment)
        assert runner.last_successful_at is not None
        self.assertEqual(runner.last_successful_at, datetime(2026, 9, 29, 2, 0, tzinfo=timezone.utc))
        # Der heutige Termin ist gelaufen, deshalb steht nichts nach.
        self.assertFalse(runner.is_due(moment))

    def test_a_restart_catches_up_an_overdue_backup(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "school-20250801T020000Z.dump").write_bytes(b"sehr alt")
        moment = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
        runner = BackupRunner(self.config(), now=moment)
        assert runner.last_successful_at is not None
        self.assertLess(runner.last_successful_at, datetime(2025, 8, 2, tzinfo=timezone.utc))
        self.assertTrue(runner.is_due(moment))

    def test_a_missed_fire_time_is_made_up_after_a_restart(self) -> None:
        # Der Prozess war um 02:00 nicht da und startet um 02:05 wieder. Der letzte
        # Stand ist vom Vortag, also wird der versaeumte Termin nachgeholt.
        (self.directory / "school-20260929T020000Z.dump").write_bytes(b"gestern")
        moment = datetime(2026, 9, 30, 2, 5, tzinfo=timezone.utc)
        runner = BackupRunner(self.config(), now=moment)
        self.assertTrue(runner.is_due(moment))

    def test_the_next_due_time_is_the_next_slot_not_the_current_minute(self) -> None:
        moment = datetime(2026, 9, 30, 2, 0, tzinfo=timezone.utc)
        runner = BackupRunner(self.config(), now=moment)
        status = runner.status(moment)
        assert status["next_due_at"] is not None
        # Nachzuholen ist noch etwas, deshalb zeigt der Status auf jetzt und nicht
        # auf den Termin, der gerade laeuft.
        self.assertEqual(status["next_due_at"], moment.isoformat())
        with mock.patch("worker.backup._pg_dump_command", return_value=self.fake_command()):
            runner.run_once(moment)
        status = runner.status(moment)
        upcoming = datetime.fromisoformat(str(status["next_due_at"]))
        self.assertGreater(upcoming, moment)
        self.assertEqual(upcoming, datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc))

