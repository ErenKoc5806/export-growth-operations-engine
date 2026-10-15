import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pilot_engine.secrets import SecretStore
from pilot_engine.windows_files import WindowsPrivateFiles, _FileAPI, _file_name
from pilot_engine.windows_security import current_process_sid, inspect_resource


class PrivateFileNameTests(unittest.TestCase):
    def test_rejects_device_stream_and_path_alias_names(self):
        for name in ("../TOKEN", "A/B", "A\\B", "TOKEN:stream", "TOKEN.", "TOKEN ",
                     "CON", "con.txt", "NUL", "PRN.json", "COM1", "LPT9.txt", "", ".", 7):
            with self.subTest(name=name), self.assertRaises(ValueError):
                _file_name(name)
        for name in ("MAIL_TOKEN", "PO-123.pdf", "evidence_123.bin"):
            self.assertEqual(_file_name(name), name)

    @unittest.skipIf(os.name == "nt", "Non-Windows boundary")
    def test_no_native_handle_fallback_on_posix(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(RuntimeError):
            WindowsPrivateFiles(directory)


@unittest.skipUnless(os.name == "nt", "Requires native Windows sharing and ACL semantics")
class NativeWindowsFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        self.root.mkdir()
        self.sid = current_process_sid()
        self.icacls(self.root, "/setowner", "*" + self.sid)
        self.icacls(self.root, "/inheritance:r", "/grant:r", "*" + self.sid + ":(OI)(CI)F")
        self.files = WindowsPrivateFiles(self.root)

    def icacls(self, path, *args):
        subprocess.run(["icacls.exe", str(path), *args], check=True, capture_output=True)

    def test_create_checks_owner_acl_and_never_overwrites(self):
        self.files.write_new("PO-123.txt", b"invented evidence")
        security = inspect_resource(self.root / "PO-123.txt", owner_sid=self.sid,
                                    allowed_sids=frozenset({self.sid}))
        self.assertEqual(security.owner_sid, self.sid)
        self.assertEqual(self.files.read_bytes("PO-123.txt", max_bytes=100), b"invented evidence")
        with self.assertRaises(FileExistsError):
            self.files.write_new("PO-123.txt", b"replacement")
        self.assertEqual((self.root / "PO-123.txt").read_bytes(), b"invented evidence")
        self.files.write_new("EMPTY", b"")
        self.assertEqual(self.files.read_bytes("EMPTY", max_bytes=1), b"")
        with self.assertRaises(ValueError):
            self.files.read_bytes("EMPTY", max_bytes=0)

    def test_read_limit_and_secret_validation_redaction_and_rotation(self):
        self.files.write_new("TOKEN", b"invented-secret\r\n")
        source = SecretStore(self.root)
        secret = source.get("TOKEN")
        self.assertEqual(secret.reveal(), "invented-secret")
        self.assertNotIn("invented-secret", repr(secret) + str(secret) + repr(source))
        self.files.write_new("NEXT", b"rotated\n")
        os.replace(self.root / "NEXT", self.root / "TOKEN")
        self.assertEqual(source.get("TOKEN").reveal(), "rotated")
        with self.assertRaises(KeyError):
            source.get("MISSING")
        for index, content in enumerate((b"", b"one\ntwo", b"\xff", b"nul\x00", b"x" * 65537)):
            name = "BAD" + str(index)
            self.files.write_new(name, content)
            with self.subTest(name=name), self.assertRaises(ValueError):
                source.get(name)
        self.files.write_new("LIMIT", b"x" * 65536)
        self.assertEqual(len(source.get("LIMIT").reveal()), 65536)

    def test_checked_leaf_cannot_be_replaced_or_written_before_read(self):
        self.files.write_new("TOKEN", b"original")
        self.files.write_new("REPLACEMENT", b"untrusted replacement")
        original = _FileAPI.handle_security
        attacks = []

        def check_then_attack(api, handle):
            security = original(api, handle)
            if not security.is_directory:
                with self.assertRaises(PermissionError):
                    os.replace(self.root / "REPLACEMENT", self.root / "TOKEN")
                with self.assertRaises(PermissionError):
                    with (self.root / "TOKEN").open("wb"):
                        pass
                attacks.append(True)
            return security

        with patch.object(_FileAPI, "handle_security", check_then_attack):
            self.assertEqual(self.files.read_bytes("TOKEN", max_bytes=100), b"original")
        self.assertEqual(attacks, [True])
        # Locks are released after the read, so normal owner-controlled rotation works.
        os.replace(self.root / "REPLACEMENT", self.root / "TOKEN")
        self.assertEqual(self.files.read_bytes("TOKEN", max_bytes=100), b"untrusted replacement")

    def test_directory_is_pinned_and_failures_release_handles(self):
        moved = Path(self.temp.name) / "moved"
        with self.files._pinned_directory():
            with self.assertRaises(PermissionError):
                self.root.rename(moved)
        self.files.write_new("TOKEN", b"invented")
        with self.assertRaises(ValueError):
            self.files.read_bytes("TOKEN", max_bytes=1)
        self.root.rename(moved)
        moved.rename(self.root)

    def test_checked_new_file_stays_locked_until_content_is_written(self):
        self.files.write_new("REPLACEMENT", b"different file")
        original = _FileAPI.handle_security
        attacks = []

        def check_then_attack(api, handle):
            security = original(api, handle)
            if not security.is_directory:
                with self.assertRaises(PermissionError):
                    os.replace(self.root / "REPLACEMENT", self.root / "UPLOAD")
                attacks.append(True)
            return security

        with patch.object(_FileAPI, "handle_security", check_then_attack):
            self.files.write_new("UPLOAD", b"invented upload bytes")
        self.assertEqual(attacks, [True])
        self.assertEqual(self.files.read_bytes("UPLOAD", max_bytes=100), b"invented upload bytes")

    def test_rechecks_directory_and_leaf_permissions_on_every_read(self):
        self.files.write_new("TOKEN", b"invented")
        source = SecretStore(self.root)
        self.icacls(self.root, "/grant", "*S-1-1-0:(OI)(CI)R")
        with self.assertRaises(PermissionError):
            source.get("TOKEN")
        with self.assertRaises(PermissionError):
            self.files.write_new("SHOULD_NOT_EXIST", b"private")
        self.assertFalse((self.root / "SHOULD_NOT_EXIST").exists())
        self.icacls(self.root, "/remove:g", "*S-1-1-0")
        self.icacls(self.root / "TOKEN", "/grant", "*S-1-1-0:R")
        with self.assertRaises(PermissionError):
            source.get("TOKEN")
        self.icacls(self.root / "TOKEN", "/remove:g", "*S-1-1-0")
        self.assertEqual(source.get("TOKEN").reveal(), "invented")

    def test_rejects_junction_hardlink_and_directory_instead_of_secret(self):
        self.files.write_new("TOKEN", b"invented")
        os.link(self.root / "TOKEN", self.root / "LINK")
        with self.assertRaisesRegex(PermissionError, "hard links"):
            SecretStore(self.root).get("TOKEN")
        (self.root / "LINK").unlink()
        (self.root / "DIRECTORY").mkdir()
        with self.assertRaises(PermissionError):
            SecretStore(self.root).get("DIRECTORY")
        alias = Path(self.temp.name) / "alias"
        subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(alias), str(self.root)],
                       check=True, capture_output=True)
        try:
            with self.assertRaisesRegex(PermissionError, "[Rr]eparse"):
                SecretStore(alias).get("TOKEN")
        finally:
            alias.rmdir()


if __name__ == "__main__":
    unittest.main()
