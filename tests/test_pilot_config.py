import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pilot_engine.__main__ import main
from pilot_engine.config import AppConfig, Environment
from pilot_engine.domain import DEFAULT_PILOT_SCOPE


class PilotConfigTests(unittest.TestCase):
    def test_synthetic_default_and_test_environment(self):
        default = AppConfig.from_env({})
        self.assertEqual(default.environment, Environment.DEVELOPMENT)
        self.assertEqual(default.scope, DEFAULT_PILOT_SCOPE)
        self.assertIsNone(default.data_dir)
        self.assertEqual(AppConfig.from_env({"EGO_ENV": "test"}).environment, Environment.TEST)
        with self.assertRaisesRegex(ValueError, "EGO_DATA_DIR"):
            default.open_store()

    def test_pilot_requires_explicit_scope_and_private_existing_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            values = {"EGO_ENV": "pilot", "EGO_HS6": "732690", "EGO_COUNTRY": "DE",
                      "EGO_CURRENCY": "EUR", "EGO_DATA_DIR": directory}
            config = AppConfig.from_env(values)
            self.assertEqual(config.open_store().scope, DEFAULT_PILOT_SCOPE)
            self.assertEqual((path / "pilot.sqlite3").stat().st_mode & 0o777, 0o600)
            for missing in ("EGO_HS6", "EGO_COUNTRY", "EGO_CURRENCY", "EGO_DATA_DIR"):
                with self.subTest(missing=missing), self.assertRaises(ValueError):
                    AppConfig.from_env({key: value for key, value in values.items() if key != missing})
            path.chmod(0o755)
            with self.assertRaises(PermissionError):
                AppConfig.from_env(values)

    def test_invalid_values_and_symlink_are_rejected(self):
        for field, bad in (("EGO_ENV", "production"), ("EGO_HS6", "７３２６９０"),
                           ("EGO_COUNTRY", "de"), ("EGO_CURRENCY", "EURO")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                AppConfig.from_env({field: bad})
        with tempfile.TemporaryDirectory() as directory:
            alias = Path(directory) / "alias"
            alias.symlink_to(directory, target_is_directory=True)
            with self.assertRaises(ValueError):
                AppConfig.from_env({"EGO_DATA_DIR": str(alias)})

    def test_synthetic_cli_refuses_pilot_environment(self):
        with patch.dict(os.environ, {"EGO_ENV": "pilot", "EGO_HS6": "732690",
                                     "EGO_COUNTRY": "DE", "EGO_CURRENCY": "EUR"}, clear=True):
            with tempfile.TemporaryDirectory() as directory:
                os.environ["EGO_DATA_DIR"] = directory
                with self.assertRaisesRegex(RuntimeError, "synthetic CLI"):
                    main()


if __name__ == "__main__":
    unittest.main()
