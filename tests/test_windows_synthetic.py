import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from pilot_engine.access import LocalAccess, Role
from pilot_engine.config import AppConfig
from pilot_engine.operator_web import OperatorWeb, _dashboard
from pilot_engine.synthetic_demo import create_demo_handoff


class WindowsSyntheticTests(unittest.TestCase):
    def test_windows_dev_uses_local_app_data_and_never_enables_pilot(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"LOCALAPPDATA": temp}):
            values = {"LOCALAPPDATA": temp, "EGO_ENV": "development"}
            with (patch("pilot_engine.config._windows_host", return_value=True),
                  patch("pilot_engine.store._windows_host", return_value=True)):
                config = AppConfig.from_env(values)
                self.assertEqual(config.data_dir, Path(temp) / "ExportGrowthOperations" /
                                 "SyntheticTrial")
                store = config.open_store()
                with closing(store._connect()) as db:
                    self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 28)
                app = OperatorWeb(config)
                create_demo_handoff(app.store)
                self.assertIn("Örnek vakayı başlat".encode(), _dashboard(app))
                with self.assertRaisesRegex(RuntimeError, "Canlı pilot"):
                    AppConfig.from_env({**values, "EGO_ENV": "pilot"})
                with self.assertRaisesRegex(ValueError, "EGO_DATA_DIR"):
                    AppConfig.from_env({**values, "EGO_DATA_DIR": temp})
                with self.assertRaisesRegex(ValueError, "gizli anahtar"):
                    AppConfig.from_env({**values, "EGO_SECRET_DIR": temp})
                with self.assertRaisesRegex(PermissionError, "yerel deneme klasöründe"):
                    type(store)(Path(temp) / "other.sqlite3", synthetic_windows=True)

    def test_windows_account_label_only_authorizes_its_local_trial(self):
        with patch("pilot_engine.access.current_principal", return_value="windows:tester"):
            access = LocalAccess.single_operator()
            self.assertEqual(access.decision("EDIT_EXECUTE"), ("windows:tester", True))
        with patch("pilot_engine.access.current_principal", return_value="windows:other"):
            self.assertEqual(access.decision("EDIT_EXECUTE"), ("windows:other", False))


if __name__ == "__main__":
    unittest.main()
