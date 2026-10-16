"""Fictional disposable data only; these tests do not supply manufacturer/IT review."""

import json
import os
import sqlite3
import subprocess
import sys
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import test_live_intake as intake_tests
from pilot_engine import live_migration as migration
from pilot_engine.access import LocalAccess, Role, current_principal
from pilot_engine.live_intake import LiveIntake
from pilot_engine.live_operator import LiveOperator
from pilot_engine.live_retention import Retention, TOMBSTONE


class LiveRetentionTests(unittest.TestCase):
    snapshot = intake_tests.LiveIntakeTests.snapshot
    enroll = intake_tests.LiveIntakeTests.enroll
    save = intake_tests.LiveIntakeTests.save
    confirm = intake_tests.LiveIntakeTests.confirm

    def setUp(self):
        intake_tests.LiveIntakeTests.setUp(self)
        self.pilot = self.enroll()
        self.identity = self.save()
        self.confirm(self.identity)
        with self.intake.transaction(write=False) as db:
            self.document = db.execute("SELECT document_id FROM live_source_permission").fetchone()[0]
        self.backup_copy = self.recovery / "with-live.sqlite3"
        self.restore_copy = self.restore / "with-live.sqlite3"

    def begin(self, kind="DOCUMENT", target=None):
        target = target or self.document
        plan = self.retention.plan(kind, target)
        return self.retention.begin(kind, target, plan["plan_sha256"], "FICTIONAL-POLICY", reviewed=True)

    def test_document_purge_removes_bytes_blocks_approval_download_and_reupload(self):
        second = self.save(source_ref="TEST-SHARED-SOURCE")
        plan = self.retention.plan("DOCUMENT", self.document)
        self.assertEqual(plan["affected_rows"]["live_source_permission"], 2)
        self.assertTrue(plan["retains_live_text"])
        self.assertNotIn("Fixture Approver", json.dumps(plan))
        operation = self.begin()
        with self.assertRaisesRegex(ValueError, "pending"):
            self.intake.view(self.identity)
        result = self.retention.resume(operation)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertFalse(result["physical_erasure_claimed"])
        for identity in (self.identity, second):
            self.assertIn("silinmiş", self.intake.view(identity)["status"])
        with self.assertRaisesRegex(ValueError, "silinmiş"):
            self.confirm(self.identity)
        with self.intake.transaction(write=False) as db:
            with self.assertRaisesRegex(ValueError, "silinmiş"):
                self.intake.read_document(db, self.document)
            self.assertEqual(db.execute("SELECT content FROM live_document WHERE id=?",
                                       (self.document,)).fetchone()[0], TOMBSTONE)
        with self.assertRaisesRegex(ValueError, "silinmiş"):
            self.save(source_ref="TEST-REUPLOAD")
        app = LiveOperator(self.path, retention_ledger=self.ledger)
        body, _ = app.page("/record", {"id": [self.identity]})
        self.assertIn("silinmiş, kullanılamaz".encode(), body)
        with self.assertRaises(ValueError):
            app.page("/document", {"id": [self.document]})
        self.assertNotIn(intake_tests.DOCUMENT, self.path.read_bytes())
        self.assertEqual(self.snapshot(self.path), self.before)
        self.assertEqual(self.retention.resume(operation)["status"], "COMPLETE")

    def test_retire_removes_all_live_payload_people_and_documents_preserving_legacy(self):
        operation = self.begin("PILOT", self.pilot)
        self.retention.resume(operation)
        with closing(migration._connect(self.path)) as db:
            for table in ("live_pilot", "live_authorization", "live_source", "live_intake_revision",
                          "live_confirmation", "live_document"):
                self.assertEqual(db.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)
            self.assertGreater(db.execute("SELECT count(*) FROM live_document_purge").fetchone()[0], 0)
            migration._check(db)
            self.assertEqual(migration._live_version(db), 3)
        for marker in (intake_tests.DOCUMENT, b"Fixture Approver", b"FICTIONAL manufacturer approval"):
            self.assertNotIn(marker, self.path.read_bytes())
        self.assertEqual(self.snapshot(self.path), self.before)
        self.assertIsNone(self.intake.view()["pilot"])
        # Even a new pilot cannot re-upload the erased bytes under a new UUID.
        self.enroll_new_with_different_evidence()
        with self.assertRaisesRegex(ValueError, "quarantined"):
            self.save(source_ref="TEST-REINTRODUCE")
        self.assertEqual(self.intake.view()["items"], [])

    def enroll_new_with_different_evidence(self):
        self.intake.enroll(intake_tests.AUTH, b"FICTIONAL fresh manufacturer evidence",
                          b"FICTIONAL fresh IT evidence")

    def test_stale_plan_wrong_policy_unknown_target_and_denied_admin_do_not_begin(self):
        plan = self.retention.plan("DOCUMENT", self.document)
        self.confirm(self.identity, "REVOKE")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.retention.begin("DOCUMENT", self.document, plan["plan_sha256"], "FICTIONAL-POLICY", reviewed=True)
        with self.assertRaisesRegex(ValueError, "current reviewed policy"):
            self.retention.begin("DOCUMENT", self.document, "a" * 64, "WRONG-POLICY", reviewed=True)
        with self.assertRaises(ValueError):
            self.retention.plan("DOCUMENT", "LIVE-UNKNOWN")
        with self.assertRaisesRegex(ValueError, "Explicit"):
            self.retention.begin("DOCUMENT", self.document, "a" * 64, "FICTIONAL-POLICY")
        operator = Retention(self.path, self.ledger, LocalAccess({current_principal(): Role.OPERATOR}))
        with self.assertRaises(PermissionError):
            operator.plan("DOCUMENT", self.document)
        self.assertEqual(self.retention.inventory()["operations"], [])

    def test_mutation_failure_rolls_back_content_and_every_guard_and_can_resume(self):
        operation = self.begin()
        original = self.retention._mutate

        def fail(db, intent):
            original(db, intent)
            raise RuntimeError("injected failure after mutation and guard recreation")

        with patch.object(self.retention, "_mutate", fail):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.retention.resume(operation)
        with closing(migration._connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT content FROM live_document WHERE id=?",
                                       (self.document,)).fetchone()[0], intake_tests.DOCUMENT)
            self.assertEqual(migration._live_version(db), 3)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("UPDATE live_document SET content=?", (b"unauthorized mutation",))
        with self.assertRaisesRegex(ValueError, "pending"):
            LiveIntake(self.path, retention_ledger=self.ledger)
        self.retention.resume(operation)
        self.assertIn("silinmiş", self.intake.view(self.identity)["status"])

    def test_compaction_failure_keeps_durable_pending_intent_then_retry_finishes(self):
        operation = self.begin()
        with patch.object(self.retention, "_compact", side_effect=OSError("injected disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                self.retention.resume(operation)
        self.assertEqual(self.retention.inventory()["operations"][0]["state"], "PENDING")
        with self.assertRaisesRegex(ValueError, "pending"):
            self.intake.view()
        self.assertEqual(self.retention.resume(operation)["status"], "COMPLETE")
        self.assertNotIn(intake_tests.DOCUMENT, self.path.read_bytes())

    def test_retirement_retry_after_commit_and_compaction_failure_is_idempotent(self):
        operation = self.begin("PILOT", self.pilot)
        with patch.object(self.retention, "_compact", side_effect=OSError("interrupted retirement")):
            with self.assertRaises(OSError):
                self.retention.resume(operation)
        self.retention.resume(operation)
        self.assertIsNone(self.intake.view()["pilot"])
        self.assertEqual(self.retention.resume(operation)["status"], "COMPLETE")

    def test_registered_backup_restores_full_data_but_old_copy_is_quarantined_after_purge(self):
        result = self.retention.backup(self.backup_copy, self.restore_copy, "2098-01-01T00:00:00Z")
        self.assertTrue(result["verified"])
        self.assertEqual(len(self.retention.inventory()["copies"]), 2)
        self.assertFalse(self.retention.verify_restore(self.restore_copy)["activation_authorized"])
        operation = self.begin()
        self.retention.resume(operation)
        with self.assertRaisesRegex(ValueError, "quarantined"):
            self.retention.verify_restore(self.backup_copy)
        # Supported app refuses a replayed old main database against current control.
        os.replace(self.restore_copy, self.path)
        with self.assertRaisesRegex(ValueError, "quarantined"):
            LiveIntake(self.path, retention_ledger=self.ledger)
        with self.assertRaisesRegex(ValueError, "quarantined"):
            self.retention.resume(operation)

    def test_old_pilot_copy_is_quarantined_after_retirement_and_missing_ledger_is_rejected(self):
        self.retention.backup(self.backup_copy, self.restore_copy, "2098-01-01T00:00:00Z")
        self.retention.resume(self.begin("PILOT", self.pilot))
        with self.assertRaisesRegex(ValueError, "retired pilot"):
            self.retention.verify_restore(self.restore_copy)
        with self.assertRaises(ValueError):
            LiveIntake(self.path)
        with self.assertRaises(FileNotFoundError):
            LiveIntake(self.path, retention_ledger=self.retention_dir / "missing.sqlite3")
        with self.assertRaises(ValueError):
            Retention(self.path, self.data / "unsafe-ledger.sqlite3")

    def test_copy_disposal_rejects_wrong_hash_and_supports_reviewed_idempotent_disposition(self):
        result = self.retention.backup(self.backup_copy, self.restore_copy, "2098-01-01T00:00:00Z")
        identity = result["backup_id"]
        with self.assertRaisesRegex(ValueError, "hash changed"):
            self.retention.dispose_copy(identity, "0" * 64, "FIXTURE-DISPOSITION", reviewed=True)
        self.assertTrue(self.backup_copy.exists())
        checksum = migration._sha256(self.backup_copy)
        with self.assertRaises(ValueError):
            self.retention.dispose_copy(identity, checksum, "FIXTURE-DISPOSITION")
        self.retention.dispose_copy(identity, checksum, "FIXTURE-DISPOSITION", reviewed=True)
        self.assertFalse(self.backup_copy.exists())
        self.assertTrue(self.restore_copy.exists())
        self.assertEqual(self.retention.dispose_copy(identity, checksum, "FIXTURE-DISPOSITION",
                                                    reviewed=True)["status"], "DISPOSED")
        self.assertEqual({row["state"] for row in self.retention.inventory()["copies"]}, {"DISPOSED", "ACTIVE"})
        migration._new_file(self.backup_copy, b"unexpected reappeared copy")
        with self.assertRaisesRegex(ValueError, "reappeared"):
            self.retention.dispose_copy(identity, checksum, "FIXTURE-DISPOSITION", reviewed=True)
        self.assertTrue(any(row["reappeared_after_disposal"] for row in self.retention.inventory()["copies"]))
        self.assertEqual(self.backup_copy.read_bytes(), b"unexpected reappeared copy")

    def test_failed_backup_retains_registered_partial_copy_for_inspection(self):
        original = migration._copy_database

        def fail(source, target):
            original(source, target)
            raise OSError("injected partial backup failure")

        with patch("pilot_engine.live_retention._copy_database", fail):
            with self.assertRaises(OSError):
                self.retention.backup(self.backup_copy, self.restore_copy, "2098-01-01T00:00:00Z")
        inventory = self.retention.inventory()
        self.assertEqual({row["state"] for row in inventory["copies"]}, {"PENDING"})
        self.assertTrue(self.backup_copy.exists())
        self.assertFalse(self.restore_copy.exists())
        self.assertEqual(self.intake.view(self.identity)["status"], "APPROVE")

    def test_policy_expiry_blocks_use_until_review_and_copy_expiry_is_visible(self):
        self.retention.backup(self.backup_copy, self.restore_copy, "2098-01-01T00:00:00Z")
        later = datetime(2099, 2, 1, tzinfo=timezone.utc)
        with patch("pilot_engine.live_retention._now", return_value=later):
            with self.assertRaisesRegex(ValueError, "retention expiry"):
                self.intake.view()
            self.assertTrue(all(row["expired"] for row in self.retention.inventory()["copies"]))
            self.retention.review("Fixture backup owner", "2100-01-01T00:00:00Z", "FIXTURE-RENEWED-POLICY",
                                  "FIXTURE-STORAGE-REVIEW", reviewed=True)
        self.assertEqual(self.intake.view(self.identity)["status"], "APPROVE")

    def test_cli_plan_begin_resume_and_inventory_use_supported_commands(self):
        base = [sys.executable, "-m", "pilot_engine.live_retention", "--database", str(self.path),
                "--ledger", str(self.ledger)]

        def run(*args):
            result = subprocess.run(base + list(args), check=True, capture_output=True, text=True)
            self.assertNotIn("ResourceWarning:", result.stderr)
            return json.loads(result.stdout)

        plan = run("plan", "--kind", "DOCUMENT", "--target", self.document)
        operation = run("begin", "--kind", "DOCUMENT", "--target", self.document,
                        "--plan-sha256", plan["plan_sha256"], "--policy-ref", "FICTIONAL-POLICY", "--reviewed")
        self.assertEqual(run("resume", "--operation", operation["operation_id"])["status"], "COMPLETE")
        self.assertEqual(run("inventory")["operations"][0]["state"], "COMPLETE")

    def test_active_intake_lease_prevents_a_purge_intent_from_racing_a_transaction(self):
        plan = self.retention.plan("DOCUMENT", self.document)
        with self.intake.transaction(write=False):
            with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                self.retention.begin("DOCUMENT", self.document, plan["plan_sha256"],
                                     "FICTIONAL-POLICY", reviewed=True)
        self.assertEqual(self.retention.inventory()["operations"], [])
        self.retention.resume(self.begin())

    def test_disposal_interrupted_after_unlink_can_resume_with_the_recorded_hash(self):
        result = self.retention.backup(self.backup_copy, self.restore_copy, "2098-01-01T00:00:00Z")
        checksum = migration._sha256(self.backup_copy)
        original = Path.unlink

        def interrupted(path, *args, **kwargs):
            original(path, *args, **kwargs)
            if path == self.backup_copy:
                raise OSError("injected interruption after unlink")

        with patch.object(Path, "unlink", interrupted):
            with self.assertRaisesRegex(OSError, "after unlink"):
                self.retention.dispose_copy(result["backup_id"], checksum, "FIXTURE-DISPOSITION", reviewed=True)
        self.assertFalse(self.backup_copy.exists())
        with self.assertRaisesRegex(ValueError, "hash changed"):
            self.retention.dispose_copy(result["backup_id"], "0" * 64, "FIXTURE-DISPOSITION", reviewed=True)
        self.assertEqual(self.retention.dispose_copy(result["backup_id"], checksum,
                         "FIXTURE-DISPOSITION", reviewed=True)["status"], "DISPOSED")

    def test_populated_v2_upgrade_preserves_evidence_and_requires_existing_copy_inventory(self):
        # Recreate the released v2 boundary with populated fictional intake data.
        with closing(migration._connect(self.path)) as db, db:
            guard = db.execute("SELECT sql FROM sqlite_master WHERE name='live_schema_migration_no_delete'").fetchone()[0]
            db.execute("DROP TRIGGER live_schema_migration_no_delete")
            receipt = db.execute("SELECT * FROM live_schema_migration WHERE version=3").fetchone()
            db.execute("DELETE FROM live_schema_migration WHERE version=3")
            db.execute("INSERT INTO live_schema_migration VALUES (2,?,?,?,?)", receipt[1:])
            db.execute(guard)
            for table in migration.RETENTION_TABLES:
                db.execute("DROP TABLE " + table)
            self.assertEqual(migration._live_version(db), 2)
            before = migration._snapshot(db, include_live=True)
        migration.migrate(self.path, self.backup_copy, self.restore_copy)
        for path in (self.backup_copy, self.restore_copy):
            with closing(migration._connect(path)) as db:
                self.assertEqual(migration._snapshot(db, include_live=True), before)
        new_ledger = self.retention_dir / "upgraded-control.sqlite3"
        retention = Retention(self.path, new_ledger)
        with self.assertRaisesRegex(ValueError, "existing recovery copies"):
            retention.initialize("Fixture owner", "2099-01-01T00:00:00Z", "FIXTURE-POLICY",
                                 "FIXTURE-STORAGE", reviewed=True)
        self.assertFalse(new_ledger.exists())
        retention.initialize("Fixture owner", "2099-01-01T00:00:00Z", "FIXTURE-POLICY", "FIXTURE-STORAGE",
            reviewed=True, existing_copies=(self.backup_copy, self.restore_copy), copies_until="2098-01-01T00:00:00Z")
        self.assertEqual(len(retention.inventory()["copies"]), 2)
        self.assertEqual(LiveIntake(self.path, retention_ledger=new_ledger).view(self.identity)["status"], "APPROVE")

    def test_control_wal_mode_is_rejected_instead_of_losing_reader_exclusion(self):
        with closing(migration._connect(self.ledger)) as db:
            db.execute("PRAGMA journal_mode=WAL")
        with self.assertRaisesRegex(ValueError, "DELETE journal mode"):
            self.intake.view()

    @unittest.skipIf(os.name == "nt", "POSIX WAL permission fixture; native normal-mode purge is covered")
    def test_purge_checkpoints_main_wal_and_compacts_to_offline_delete_mode(self):
        with closing(migration._connect(self.path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
        self.retention.resume(self.begin())
        with closing(migration._connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertNotIn(intake_tests.DOCUMENT, self.path.read_bytes())
        self.assertFalse(Path(str(self.path) + "-wal").exists())


if __name__ == "__main__":
    unittest.main()
