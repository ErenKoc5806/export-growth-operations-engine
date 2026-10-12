import os
import tempfile
import unittest
from contextlib import closing
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlencode
from unittest.mock import patch

from pilot_engine.access import LocalAccess, Role
from pilot_engine.config import AppConfig
from pilot_engine.operator_web import OperatorWeb, _dashboard, make_handler
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
                    self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 29)
                app = OperatorWeb(config)
                create_demo_handoff(app.store)
                self.assertIn("Örnek vakayı başlat".encode(), _dashboard(app))
                if os.name == "nt":
                    with ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app)) as server:
                        thread = Thread(target=server.serve_forever, daemon=True)
                        thread.start()
                        try:
                            connection = HTTPConnection("127.0.0.1", server.server_port)
                            connection.request("GET", "/?token=" + app.session)
                            response = connection.getresponse()
                            cookie = response.getheader("Set-Cookie").split(";", 1)[0]
                            self.assertEqual(response.status, 303)
                            response.read()
                            connection.request("GET", "/", headers={"Cookie": cookie})
                            response = connection.getresponse()
                            self.assertEqual(response.status, 200)
                            self.assertIn("Örnek vakayı başlat".encode(), response.read())
                            body = urlencode({"csrf": app.csrf, "confirm": "yes"})
                            connection.request("POST", "/demo", body=body, headers={
                                "Cookie": cookie,
                                "Origin": "null",
                                "Sec-Fetch-Site": "same-origin",
                                "Content-Type": "application/x-www-form-urlencoded",
                            })
                            response = connection.getresponse()
                            self.assertEqual(response.status, 303, response.read())
                            case_url = response.getheader("Location")
                            response.read()
                            connection.request("GET", case_url, headers={"Cookie": cookie})
                            response = connection.getresponse()
                            self.assertEqual(response.status, 200)
                            self.assertIn("Satış fırsatını aç".encode(), response.read())
                            connection.close()
                        finally:
                            server.shutdown()
                            thread.join(timeout=5)
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
