import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from pilot_engine.__main__ import main
from pilot_engine.config import AppConfig
from pilot_engine.observability import ErrorCode, Event, Outcome, PilotLogger, new_correlation_id
from pilot_engine.workflow import PilotValidationError


class PilotObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "operations.jsonl"

    def test_fixed_schema_permissions_and_correlation(self):
        logger = PilotLogger(self.path)
        identifier = new_correlation_id()
        logger.emit(Event.SYNTHETIC_RUN, Outcome.SUCCESS, identifier, duration_ms=5)
        record = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(record), {"occurred_at_utc", "event", "outcome",
                                       "correlation_id", "error_code", "duration_ms"})
        self.assertEqual(record["correlation_id"], identifier)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        for arguments in (("SYNTHETIC_RUN", Outcome.SUCCESS, identifier),
                          (Event.SYNTHETIC_RUN, Outcome.FAILED, identifier),
                          (Event.SYNTHETIC_RUN, Outcome.SUCCESS, "buyer@example.com")):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                logger.emit(*arguments)
        self.assertEqual(len(self.path.read_text(encoding="utf-8").splitlines()), 1)

    def test_private_path_and_symlink_required(self):
        logger = PilotLogger(self.path)
        self.path.symlink_to(self.directory / "other")
        with self.assertRaises(OSError):
            logger.emit(Event.SYNTHETIC_RUN, Outcome.SUCCESS, new_correlation_id())
        self.path.unlink()
        self.path.write_text("", encoding="utf-8")
        self.path.chmod(0o644)
        with self.assertRaises(PermissionError):
            logger.emit(Event.SYNTHETIC_RUN, Outcome.SUCCESS, new_correlation_id())
        self.path.unlink()
        self.directory.chmod(0o755)
        with self.assertRaises(PermissionError):
            PilotLogger(self.path)

    def test_cli_logs_success_and_validation_failure_without_payload(self):
        config = AppConfig.from_env({"EGO_DATA_DIR": str(self.directory)})
        with contextlib.redirect_stdout(io.StringIO()):
            main(config)
        fixture = Path(__file__).resolve().parents[1] / "pilot_engine" / "fixtures" / "732690_de_synthetic.json"
        case = json.loads(fixture.read_text(encoding="utf-8"))
        case["customer_po"]["total"] = "9999.00"
        invalid = self.directory / "invalid.json"
        invalid.write_text(json.dumps(case), encoding="utf-8")
        with self.assertRaises(PilotValidationError):
            main(config, invalid)
        with self.assertRaises(FileNotFoundError):
            main(config, self.directory / "missing.json")
        content = self.path.read_text(encoding="utf-8")
        rows = [json.loads(line) for line in content.splitlines()]
        self.assertEqual([row["outcome"] for row in rows], ["SUCCESS", "FAILED", "FAILED"])
        self.assertEqual(rows[1]["error_code"], ErrorCode.VALIDATION_ERROR.value)
        self.assertEqual(rows[2]["error_code"], ErrorCode.IO_ERROR.value)
        self.assertNotIn("purchasing@example.com", content)
        self.assertNotIn("9999.00", content)


if __name__ == "__main__":
    unittest.main()
