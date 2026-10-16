import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from pilot_engine import live_migration as migration
from pilot_engine.store import PilotStore
from pilot_engine.synthetic_demo import create_demo_handoff


FIXTURE = Path(__file__).resolve().parents[1] / "pilot_engine/fixtures/732690_de_synthetic.json"


class LiveMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.env = patch.dict(os.environ, {"LOCALAPPDATA": str(root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.data = root / "ExportGrowthOperations" / "SyntheticTrial"
        self.recovery = root / "recovery"
        self.restore = root / "restore"
        self.retention_dir = root / "retention"
        for directory in (self.data, self.recovery, self.restore, self.retention_dir):
            directory.mkdir(parents=True, mode=0o700)
            if os.name == "nt":
                from pilot_engine.windows_security import current_process_sid

                sid = "*" + current_process_sid()
                for args in (("/setowner", sid),
                             ("/inheritance:r", "/grant:r", sid + ":(OI)(CI)F")):
                    subprocess.run(["icacls.exe", str(directory), *args],
                                   check=True, capture_output=True)
        self.path = self.data / "pilot.sqlite3"
        if os.name == "nt":
            from pilot_engine.windows_files import WindowsPrivateFiles

            WindowsPrivateFiles(self.data).write_new(self.path.name, b"")
        self.store = PilotStore(self.path, synthetic_windows=os.name == "nt")
        self.case = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.store.save_synthetic_case(self.case)
        self.store.record_approval(self.case["opportunity_id"], "APPROVE_QUOTE", "Q-SYN-001", 1)
        create_demo_handoff(self.store)
        self.backup = self.recovery / "before.sqlite3"
        self.rehearsal = self.restore / "restored.sqlite3"
        self.before = self.snapshot(self.path)

    def snapshot(self, path):
        with closing(migration._connect(path, readonly=True)) as db:
            return migration._snapshot(db)

    def run_migration(self):
        return migration.migrate(self.path, self.backup, self.rehearsal)

    def assert_unmigrated(self):
        self.assertEqual(self.snapshot(self.path), self.before)
        with closing(migration._connect(self.path)) as db:
            self.assertEqual(migration._live_version(db), 0)

    def test_whole_legacy_database_backup_restore_and_idempotent_restart(self):
        result = self.run_migration()
        self.assertEqual((result.status, result.legacy_version, result.live_version),
                         ("MIGRATED", 29, 3))
        for path in (self.path, self.backup, self.rehearsal):
            self.assertEqual(self.snapshot(path), self.before)
            migration._private(path)
        self.assertGreater(len(self.before["table_counts"]), 50)
        for name in ("audit_event", "approval_event", "quotation_revision", "customer_po",
                     "sales_order", "product_profile_revision", "find_handoff"):
            self.assertGreater(self.before["table_counts"][name], 0, name)
        receipt = json.loads(self.backup.with_suffix(".sqlite3.manifest.json").read_text())
        self.assertEqual(receipt["backup_sha256"], migration._sha256(self.backup))
        self.assertEqual(receipt["fingerprint"], self.before["fingerprint"])
        self.assertNotIn("Example", json.dumps(receipt))
        backup_bytes = self.backup.read_bytes()
        self.assertEqual(self.run_migration().status, "ALREADY_MIGRATED")
        self.assertEqual(self.backup.read_bytes(), backup_bytes)
        restarted = PilotStore(self.path, synthetic_windows=os.name == "nt")
        self.assertEqual(self.snapshot(self.path), self.before)
        self.assertEqual(restarted.read_summary(self.case["opportunity_id"])["order_total"],
                         "1250.00")

    def test_ddl_failure_rolls_back_and_retry_uses_new_artifacts(self):
        original = migration._apply

        def fail_after_ddl(db):
            original(db)
            raise RuntimeError("injected DDL failure")

        with patch.object(migration, "_apply", fail_after_ddl):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.run_migration()
        self.assert_unmigrated()
        self.assertTrue(self.backup.exists())
        self.assertTrue(self.rehearsal.exists())
        with self.assertRaises(FileExistsError):
            self.run_migration()
        self.assert_unmigrated()
        self.backup = self.recovery / "retry.sqlite3"
        self.rehearsal = self.restore / "retry.sqlite3"
        self.assertEqual(self.run_migration().status, "MIGRATED")

    def test_upgrade_released_v1_preserves_live_rows_in_backup_restore_and_failure(self):
        # Recreate the released #72 extension, including its append-only guards.
        with closing(migration._connect(self.path)) as db, db:
            for statement in migration._migration_statements(migration.SCHEMA.read_text()):
                db.execute(statement)
            for table in migration.BASE_TABLES:
                for action in ("UPDATE", "DELETE"):
                    db.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
                               "BEGIN SELECT RAISE(ABORT, 'live records are append-only'); END")
            db.execute("INSERT INTO live_schema_migration VALUES (1,?,?,?,?)",
                       ("a" * 64, self.before["fingerprint"], "fixture-actor", "2026-10-16"))
            db.execute("INSERT INTO live_pilot VALUES ('LIVE-P','FICTIONAL v1 pilot','TEST-AUTH',"
                       "'Fixture operator','Fixture approver','LIVE','fixture-actor','2026-10-16')")
            self.assertEqual(migration._live_version(db), 1)
            full_before = migration._snapshot(db, include_live=True)
        original = migration._apply

        def fail(db, current):
            original(db, current)
            raise RuntimeError("injected v1 upgrade failure")

        with patch.object(migration, "_apply", fail):
            with self.assertRaisesRegex(RuntimeError, "v1 upgrade"):
                self.run_migration()
        with closing(migration._connect(self.path)) as db:
            self.assertEqual(migration._live_version(db), 1)
            self.assertEqual(migration._snapshot(db, include_live=True), full_before)
        for path in (self.backup, self.rehearsal):
            with closing(migration._connect(path)) as db:
                self.assertEqual(migration._snapshot(db, include_live=True), full_before)
        self.backup = self.recovery / "upgrade-v2.sqlite3"
        self.rehearsal = self.restore / "upgrade-v2.sqlite3"
        self.assertEqual(self.run_migration().live_version, 3)
        with closing(migration._connect(self.path)) as db:
            self.assertEqual(migration._live_version(db), 3)
            self.assertEqual(db.execute("SELECT manufacturer_name FROM live_pilot").fetchone()[0],
                             "FICTIONAL v1 pilot")
            self.assertEqual(db.execute("SELECT version FROM live_schema_migration ORDER BY version").fetchall(),
                             [(1,), (3,)])
        self.assertEqual(self.snapshot(self.path), self.before)

    def test_backup_and_restore_failure_never_apply_live_ddl(self):
        original = migration._copy_database
        for failure_at in (self.backup, self.rehearsal):
            with self.subTest(failure_at=failure_at):
                def fail(source, destination):
                    if destination == failure_at:
                        raise OSError("injected recovery failure")
                    original(source, destination)

                with patch.object(migration, "_copy_database", fail):
                    with self.assertRaisesRegex(OSError, "injected"):
                        self.run_migration()
                self.assert_unmigrated()

    def test_restore_with_changed_rows_is_rejected(self):
        original = migration._copy_database

        def corrupt(source, destination):
            original(source, destination)
            if destination == self.rehearsal:
                with closing(migration._connect(destination)) as db, db:
                    db.execute("CREATE TABLE injected(value TEXT)")

        with patch.object(migration, "_copy_database", corrupt):
            with self.assertRaisesRegex(ValueError, "Restore rehearsal"):
                self.run_migration()
        self.assert_unmigrated()

    def test_changed_backup_is_rejected_before_live_ddl(self):
        original = migration._copy_database

        def change_backup(source, destination):
            original(source, destination)
            if destination == self.backup:
                with closing(migration._connect(destination)) as db, db:
                    db.execute("CREATE TABLE injected(value TEXT)")

        with patch.object(migration, "_copy_database", change_backup):
            with self.assertRaisesRegex(ValueError, "Backup does not reproduce"):
                self.run_migration()
        self.assert_unmigrated()

    def test_bad_source_foreign_keys_block_backup(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE orphan(value TEXT REFERENCES manufacturer(id))")
            db.execute("INSERT INTO orphan VALUES ('missing-manufacturer')")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "foreign key check"):
            self.run_migration()
        self.assertFalse(self.backup.exists())

    @unittest.skipIf(os.name == "nt", "POSIX mode bits; native SID-owned paths covered separately")
    def test_unsafe_source_permissions_or_symlinked_recovery_fail_closed(self):
        self.path.chmod(0o644)
        with self.assertRaises(PermissionError):
            self.run_migration()
        self.assertFalse(self.backup.exists())
        self.path.chmod(0o600)
        alias = self.recovery.parent / "alias"
        alias.symlink_to(self.recovery, target_is_directory=True)
        with self.assertRaises(PermissionError):
            migration.migrate(self.path, alias / "backup.sqlite3", self.rehearsal)
        self.assert_unmigrated()

    def test_backup_excludes_concurrent_writer(self):
        original = migration._copy_database
        attempts = []

        def locked_copy(source, destination):
            with closing(sqlite3.connect(self.path, timeout=0)) as writer:
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    writer.execute("BEGIN IMMEDIATE")
                attempts.append(destination)
            original(source, destination)

        with patch.object(migration, "_copy_database", locked_copy):
            self.run_migration()
        self.assertEqual(attempts, [self.backup, self.rehearsal])

    @unittest.skipIf(os.name == "nt", "POSIX WAL permissions; native Windows normal mode covered")
    def test_wal_committed_rows_are_included_without_raw_file_copy(self):
        with closing(migration._connect(self.path)) as keeper:
            keeper.execute("PRAGMA journal_mode=WAL")
            keeper.execute("CREATE TABLE extra_legacy(value BLOB)")
            keeper.execute("INSERT INTO extra_legacy VALUES (?)", (b"committed in WAL",))
            keeper.commit()
            self.before = self.snapshot(self.path)
            self.run_migration()
            self.assertEqual(self.snapshot(self.backup), self.before)
            self.assertEqual(self.snapshot(self.rehearsal), self.before)

    def test_rejects_unsupported_version_and_incomplete_live_namespace(self):
        with closing(migration._connect(self.path)) as db:
            db.execute("PRAGMA user_version=28")
        with self.assertRaisesRegex(ValueError, "v29"):
            self.run_migration()
        with closing(migration._connect(self.path)) as db, db:
            db.execute("PRAGMA user_version=29")
            db.execute("CREATE TABLE live_unknown(id INTEGER)")
        with self.assertRaisesRegex(ValueError, "namespace"):
            self.run_migration()
        self.assertFalse(self.backup.exists())

    def test_rejects_existing_recovery_sidecar_or_in_place_backup(self):
        Path(str(self.backup) + "-wal").write_bytes(b"unrelated existing WAL")
        with self.assertRaises(FileExistsError):
            self.run_migration()
        self.assert_unmigrated()
        with self.assertRaisesRegex(ValueError, "outside"):
            migration.migrate(self.path, self.data / "backup", self.rehearsal)
        with self.assertRaisesRegex(ValueError, "outside"):
            migration.migrate(self.path, self.data / "nested" / "backup", self.rehearsal)

    def test_live_foreign_keys_origin_exact_confirmation_and_append_only_guards(self):
        self.run_migration()
        with closing(migration._connect(self.path)) as db, db:
            db.execute("INSERT INTO live_pilot VALUES "
                       "('LIVE-P','Fixture manufacturer','AUTH-1','Operator','Approver',"
                       "'LIVE','actor','2026-10-16')")
            source = ("LIVE-S", "LIVE-P", "PRODUCT_PROFILE", "AUTH-DOC", "a" * 64,
                      5, "LIVE", "actor", "2026-10-16")
            db.execute("INSERT INTO live_source VALUES (?,?,?,?,?,?,?,?,?)", source)
            for changed in (("LIVE-S2",) + source[1:6] + ("SYNTHETIC",) + source[7:],
                            ("SYN-S",) + source[1:],
                            ("LIVE-S2", "SYN-P") + source[2:]):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO live_source VALUES (?,?,?,?,?,?,?,?,?)", changed)
            db.execute("INSERT INTO live_intake VALUES ('LIVE-I','LIVE-P','PRODUCT_PROFILE')")
            revision = ("LIVE-I", "LIVE-P", 1, "LIVE-S", "{}", "b" * 64,
                        "LIVE", "actor", "2026-10-16")
            db.execute("INSERT INTO live_intake_revision VALUES (?,?,?,?,?,?,?,?,?)", revision)
            confirmation = ("LIVE-I", 1, "b" * 64, "APPROVE", "Approver", "AUTH-1",
                            "recorded confirmation", "RECORD-1", "reviewed", "2026-10-16",
                            "OS-actor", "2026-10-16", "OPERATOR_ATTESTATION")
            sql = "INSERT INTO live_confirmation VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            db.execute(sql, confirmation)
            for changed in (("LIVE-I", 2) + confirmation[2:],
                            confirmation[:2] + ("c" * 64,) + confirmation[3:],
                            confirmation[:-1] + ("AUTHENTICATED_APPROVER",)):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql, changed)
            for table in migration.BASE_TABLES:
                column = "version" if table == "live_schema_migration" else (
                    "sequence" if table == "live_confirmation" else (
                        "revision" if table == "live_intake_revision" else "id"))
                for sql in (f"DELETE FROM {table}", f"UPDATE {table} SET {column}={column}"):
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                        db.execute(sql)
        self.assertEqual(self.snapshot(self.path), self.before)


if __name__ == "__main__":
    unittest.main()
