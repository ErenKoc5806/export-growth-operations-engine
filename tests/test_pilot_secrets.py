import tempfile
import unittest
from pathlib import Path

from pilot_engine.config import AppConfig
from pilot_engine.secrets import SecretStore


class PilotSecretsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "private"
        self.directory.mkdir(mode=0o700)

    def write_secret(self, name, value):
        path = self.directory / name
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_read_redact_and_rotate_without_caching(self):
        path = self.write_secret("TEST_TOKEN", "first-value\n")
        config = AppConfig.from_env({"EGO_SECRET_DIR": str(self.directory)})
        source = config.open_secrets()
        value = source.get("TEST_TOKEN")
        self.assertEqual(value.reveal(), "first-value")
        self.assertNotIn("first-value", repr(value) + str(value) + repr(config))
        path.write_text("rotated-value\n", encoding="utf-8")
        self.assertEqual(source.get("TEST_TOKEN").reveal(), "rotated-value")

    def test_missing_insecure_and_malformed_files_fail_closed(self):
        source = SecretStore(self.directory)
        with self.assertRaises(KeyError):
            source.get("MISSING")
        for name in ("../other", "foo", "/absolute", "A/B", "A\nB"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                source.get(name)
        path = self.write_secret("TOKEN", "value")
        path.chmod(0o644)
        with self.assertRaises(PermissionError):
            source.get("TOKEN")
        path.chmod(0o600)
        path.write_text("\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            source.get("TOKEN")
        path.write_text("one\ntwo", encoding="utf-8")
        with self.assertRaises(ValueError):
            source.get("TOKEN")
        path.write_bytes(b"x" * 65537)
        with self.assertRaises(ValueError):
            source.get("TOKEN")

    def test_symlink_and_public_directory_are_rejected(self):
        self.write_secret("TOKEN", "value")
        (self.directory / "ALIAS").symlink_to(self.directory / "TOKEN")
        with self.assertRaises(OSError):
            SecretStore(self.directory).get("ALIAS")
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(PermissionError):
            SecretStore(alias)
        self.directory.chmod(0o755)
        with self.assertRaises(PermissionError):
            AppConfig.from_env({"EGO_SECRET_DIR": str(self.directory)})

    def test_secret_source_is_optional_until_connector_exists(self):
        with self.assertRaisesRegex(ValueError, "EGO_SECRET_DIR"):
            AppConfig.from_env({}).open_secrets()


if __name__ == "__main__":
    unittest.main()
