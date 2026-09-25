import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from worker.healthcheck import is_healthy


class HealthcheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.status_path = Path(self.temporary_directory.name) / "worker-status.json"
        self.now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_status(self, status: str, database: str, heartbeat: datetime | None = None) -> None:
        self.status_path.write_text(
            json.dumps(
                {
                    "status": status,
                    "database": database,
                    "last_heartbeat": (heartbeat or self.now).isoformat(),
                }
            ),
            encoding="utf-8",
        )

    def test_accepts_only_running_worker_with_healthy_database(self) -> None:
        self.write_status("running", "healthy")
        self.assertTrue(is_healthy(self.status_path, 120, self.now))

    def test_rejects_nonrunning_and_unhealthy_states(self) -> None:
        cases = (
            ("starting", "healthy"),
            ("degraded", "healthy"),
            ("stopping", "healthy"),
            ("stopped", "healthy"),
            ("running", "unchecked"),
            ("running", "unhealthy"),
            ("running", "stopping"),
        )
        for status, database in cases:
            with self.subTest(status=status, database=database):
                self.write_status(status, database)
                self.assertFalse(is_healthy(self.status_path, 120, self.now))

    def test_rejects_stale_heartbeat(self) -> None:
        self.write_status("running", "healthy", self.now - timedelta(seconds=121))
        self.assertFalse(is_healthy(self.status_path, 120, self.now))

    def test_rejects_naive_or_missing_heartbeat(self) -> None:
        self.write_status("running", "healthy", self.now.replace(tzinfo=None))
        self.assertFalse(is_healthy(self.status_path, 120, self.now))
        self.write_status("running", "healthy")
        payload = json.loads(self.status_path.read_text(encoding="utf-8"))
        payload.pop("last_heartbeat")
        self.status_path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertFalse(is_healthy(self.status_path, 120, self.now))


if __name__ == "__main__":
    unittest.main()
