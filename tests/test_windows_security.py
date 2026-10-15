import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pilot_engine.access import LocalAccess, current_principal
from pilot_engine.windows_security import (
    Ace, FileSecurity, WindowsAccess, WindowsRole, check_descriptor,
    current_process_sid, inspect_resource,
)


OPERATOR = "S-1-5-21-100-200-300-1001"
APPROVER = "S-1-5-21-100-200-300-1002"


class WindowsPolicyTests(unittest.TestCase):
    def test_non_posix_principal_uses_sid_without_account_name_fallback(self):
        with (patch("pilot_engine.access.os", SimpleNamespace()),
              patch("pilot_engine.windows_security.current_process_sid", return_value=OPERATOR)):
            self.assertEqual(current_principal(), "windows-sid:" + OPERATOR)
        with (patch("pilot_engine.access.os", SimpleNamespace()),
              patch("pilot_engine.windows_security.current_process_sid",
                    side_effect=PermissionError("Token lookup failed"))):
            with self.assertRaises(PermissionError):
                current_principal()

    def test_separate_accounts_and_immutable_policy(self):
        source = {OPERATOR: WindowsRole.OPERATOR, APPROVER: WindowsRole.APPROVER}
        policy = WindowsAccess(source)
        source[OPERATOR] = WindowsRole.APPROVER
        with patch("pilot_engine.windows_security.current_process_sid", return_value=OPERATOR):
            self.assertEqual(policy.decision("EDIT_EXECUTE"), ("windows-sid:" + OPERATOR, True))
            for permission in ("APPROVE_PO", "APPROVE_EXECUTE", "APPROVE_SELL_SEND", "BACKUP"):
                self.assertFalse(policy.decision(permission)[1])
        with patch("pilot_engine.windows_security.current_process_sid", return_value=APPROVER):
            self.assertTrue(policy.decision("APPROVE_PO")[1])
            self.assertTrue(policy.decision("APPROVE_SELL_SEND")[1])
            self.assertFalse(policy.decision("EDIT_EXECUTE")[1])
            self.assertFalse(policy.decision("DISPATCH_SYNTHETIC_MAIL")[1])
        with patch("pilot_engine.windows_security.current_process_sid", return_value="S-1-5-18"):
            self.assertFalse(policy.decision("READ_SUMMARY")[1])
        with self.assertRaises(TypeError):
            policy.roles_by_sid[OPERATOR] = WindowsRole.APPROVER
        for roles in ({OPERATOR: WindowsRole.OPERATOR},
                      {OPERATOR: WindowsRole.APPROVER},
                      {"S-1-1-0": WindowsRole.OPERATOR, APPROVER: WindowsRole.APPROVER}):
            with self.assertRaises(ValueError):
                WindowsAccess(roles)

    def test_descriptor_fails_closed_for_unsafe_or_unsupported_acl(self):
        def check(aces, *, owner=OPERATOR, links=1, directory=False):
            check_descriptor(FileSecurity(owner, aces, directory, links), owner_sid=OPERATOR,
                             allowed_sids=frozenset({OPERATOR}), directory=False)

        owner_ace = Ace(0, 0, 0x1F01FF, OPERATOR)
        check((owner_ace, Ace(0, 0, 0x1F01FF, "S-1-5-18")))
        for aces in (None, (), (Ace(1, 0, 1, OPERATOR),),
                     (Ace(0, 8, 1, OPERATOR),),
                     (owner_ace, Ace(0, 0, 1, "S-1-1-0")),
                     (owner_ace, Ace(0, 8, 1, "S-1-1-0")),
                     (owner_ace, Ace(0, 0, 1, APPROVER)),
                     (owner_ace, Ace(5, 0, 1, OPERATOR))):
            with self.subTest(aces=aces), self.assertRaises(PermissionError):
                check(aces)
        for kwargs in ({"owner": APPROVER}, {"links": 2}, {"directory": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(PermissionError):
                check((owner_ace,), **kwargs)
        with self.assertRaises(ValueError):
            check_descriptor(FileSecurity(OPERATOR, (owner_ace,), False, 1), owner_sid=OPERATOR,
                             allowed_sids=frozenset({OPERATOR, "S-1-1-0"}), directory=False)

    @unittest.skipIf(os.name == "nt", "Non-Windows failure boundary")
    def test_native_inspection_has_no_posix_or_username_fallback(self):
        with self.assertRaises(RuntimeError):
            current_process_sid()
        with self.assertRaises(RuntimeError):
            inspect_resource("/tmp/no-live-file", owner_sid=OPERATOR,
                             allowed_sids=frozenset({OPERATOR}))


@unittest.skipUnless(os.name == "nt", "Requires native Windows token and NTFS ACLs")
class NativeWindowsSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        self.root.mkdir()
        self.sid = current_process_sid()
        self.trustees = frozenset({self.sid})
        self.icacls(self.root, "/inheritance:r", "/grant:r", "*" + self.sid + ":(OI)(CI)F")

    def icacls(self, path, *args):
        subprocess.run(["icacls.exe", str(path), *args], check=True, capture_output=True)

    def inspect(self, path, directory=False):
        return inspect_resource(path, owner_sid=self.sid, allowed_sids=self.trustees,
                                directory=directory)

    def test_native_identity_ignores_username_environment_and_binds_local_access(self):
        self.assertTrue(self.sid.startswith("S-1-"))
        with patch.dict(os.environ, {"USERNAME": "forged", "USER": "forged", "LOGNAME": "forged"}):
            self.assertEqual(current_process_sid(), self.sid)
            self.assertEqual(current_principal(), "windows-sid:" + self.sid)
            self.assertEqual(LocalAccess.single_operator().decision("READ_SUMMARY"),
                             ("windows-sid:" + self.sid, True))

    def test_native_database_backup_restore_upload_and_secret_acl_checks(self):
        self.inspect(self.root, directory=True)
        database = self.root / "fixture.sqlite3"
        backup = self.root / "backup.sqlite3"
        restored = self.root / "restored.sqlite3"
        with closing(sqlite3.connect(database)) as source:
            source.execute("CREATE TABLE evidence (value TEXT)")
            source.execute("INSERT INTO evidence VALUES ('invented')")
            source.commit()
            with closing(sqlite3.connect(backup)) as target:
                source.backup(target)
        shutil.copyfile(backup, restored)
        upload = self.root / "invented-upload.txt"
        secret = self.root / "invented-secret.txt"
        upload.write_text("invented", encoding="utf-8")
        secret.write_text("not-a-credential", encoding="utf-8")
        for path in (database, backup, restored, upload, secret):
            self.inspect(path)
        with closing(sqlite3.connect(restored)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT value FROM evidence").fetchone()[0], "invented")
        self.icacls(backup, "/grant", "*S-1-1-0:R")
        with self.assertRaises(PermissionError):
            self.inspect(backup)
        self.icacls(backup, "/remove:g", "*S-1-1-0")
        self.inspect(backup)

    def test_native_rejects_broad_directory_acl_hardlinks_and_junctions(self):
        self.icacls(self.root, "/grant", "*S-1-1-0:(OI)(CI)R")
        with self.assertRaises(PermissionError):
            self.inspect(self.root, directory=True)
        self.icacls(self.root, "/remove:g", "*S-1-1-0")
        source = self.root / "file.txt"
        source.write_text("invented", encoding="utf-8")
        linked = self.root / "linked.txt"
        os.link(source, linked)
        with self.assertRaises(PermissionError):
            self.inspect(source)
        linked.unlink()
        junction = Path(self.temp.name) / "alias"
        subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(junction), str(self.root)],
                       check=True, capture_output=True)
        try:
            with self.assertRaises(PermissionError):
                self.inspect(junction / "file.txt")
        finally:
            junction.rmdir()


if __name__ == "__main__":
    unittest.main()
