import copy
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier

from pilot_engine.access import LocalAccess, Role
from pilot_engine.domain import OpportunityStatus
from pilot_engine.store import PilotStore
from pilot_engine.workflow import PilotValidationError


FIXTURE = Path(__file__).resolve().parents[1] / "pilot_engine" / "fixtures" / "732690_de_synthetic.json"


class PilotStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "pilot.sqlite3"
        self.case = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_restart_preserves_history_and_retries_do_not_duplicate_order(self):
        store = PilotStore(self.path)
        result = store.save_synthetic_case(self.case)
        newer = copy.deepcopy(self.case["quotation"])
        newer["revision"] = 2
        newer["unit_price"] = "13.00"
        newer["status"] = "DRAFT"
        newer.pop("approved_by")
        store.append_quotation_revision(newer)

        restarted = PilotStore(self.path)
        restarted.save_synthetic_case(self.case)
        summary = restarted.read_summary(result.opportunity_id)
        self.assertEqual(summary["quotation_revisions"], 2)
        self.assertEqual(summary["order_id"], result.sales_order["id"])
        self.assertEqual(summary["order_total"], "1250.00")
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sales_order").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT quotation_revision FROM customer_po").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE quotation_revision SET unit_price_text = '99' WHERE revision = 1")

    def test_invalid_case_rolls_back_without_partial_records(self):
        store = PilotStore(self.path)
        invalid = copy.deepcopy(self.case)
        invalid["customer_po"]["total"] = "9999.00"
        with self.assertRaises(PilotValidationError):
            store.save_synthetic_case(invalid)
        self.assertIsNone(PilotStore(self.path).read_summary(self.case["opportunity_id"]))

        store.save_synthetic_case(self.case)
        collision = copy.deepcopy(self.case)
        collision["buyer"]["company"] = "Different buyer"
        with self.assertRaisesRegex(ValueError, "different content"):
            store.save_synthetic_case(collision)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM buyer_company").fetchone()[0], 1)

    def test_transition_and_audit_are_atomic_and_contact_is_separate(self):
        store = PilotStore(self.path)
        store.save_synthetic_case(self.case)
        oid = self.case["opportunity_id"]
        with self.assertRaisesRegex(ValueError, "Illegal"):
            store.transition(oid, OpportunityStatus.ORDER_DRAFT)
        summary = PilotStore(self.path).read_summary(oid)
        self.assertEqual((summary["status"], summary["revision"]), ("SYNTHETIC_DRAFT", 1))
        self.assertNotIn("business_email", summary)
        with closing(sqlite3.connect(self.path)) as db, db:
            audit = db.execute("SELECT action, before_state, after_state FROM audit_event ORDER BY id").fetchall()
            self.assertEqual(len(audit), 1)
            self.assertEqual(audit[-1], ("SYNTHETIC_CASE_INGESTED", None, "SYNTHETIC_DRAFT"))
            self.assertEqual(db.execute("SELECT business_email FROM contact_evidence").fetchone()[0], "purchasing@example.com")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM audit_event")

    def test_sensitive_transitions_require_matching_approval_in_same_transaction(self):
        store = PilotStore(self.path)
        store.save_synthetic_case(self.case)
        oid = self.case["opportunity_id"]
        # Construct a review state without pretending the synthetic import was a live sale.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE opportunity SET status = 'QUOTE_REVIEW' WHERE id = ?", (oid,))
        with self.assertRaisesRegex(ValueError, "[Qq]uotation revision approval"):
            store.transition(oid, OpportunityStatus.QUOTE_APPROVED)
        self.assertEqual(store.read_summary(oid)["status"], "QUOTE_REVIEW")
        with self.assertRaisesRegex(ValueError, "does not match"):
            store.record_approval(oid, "APPROVE_QUOTE", "Q-SYN-001", 99)
        store.record_approval(oid, "APPROVE_QUOTE", "Q-SYN-001", 1)
        with self.assertRaisesRegex(ValueError, "already has an approval"):
            store.record_approval(oid, "APPROVE_QUOTE", "Q-SYN-001", 1)
        with closing(sqlite3.connect(self.path)) as db, db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "approvals are append-only"):
                db.execute("UPDATE approval SET decision = 'REJECTED' WHERE action = 'APPROVE_QUOTE'")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "approvals are append-only"):
                db.execute("DELETE FROM approval WHERE action = 'APPROVE_QUOTE'")
        with self.assertRaisesRegex(ValueError, "quotation revision approval"):
            store.transition(oid, OpportunityStatus.QUOTE_APPROVED,
                             approval_target_id="Q-SYN-001", approval_revision=99)
        store.transition(oid, OpportunityStatus.QUOTE_APPROVED,
                         approval_target_id="Q-SYN-001", approval_revision=1)
        store.transition(oid, OpportunityStatus.PO_REVIEW)
        with self.assertRaisesRegex(ValueError, "customer PO approval"):
            store.transition(oid, OpportunityStatus.ORDER_DRAFT,
                             approval_target_id="PO-SYN-001", approval_revision=1)
        store.record_approval(oid, "APPROVE_PO", "PO-SYN-001", 1)
        store.transition(oid, OpportunityStatus.ORDER_DRAFT,
                         approval_target_id="PO-SYN-001", approval_revision=1)
        self.assertEqual(store.read_summary(oid)["status"], "ORDER_DRAFT")

    def test_bad_revision_and_direct_sql_are_rejected(self):
        store = PilotStore(self.path)
        store.save_synthetic_case(self.case)
        for field, bad in (("unit_price", "-1"), ("quantity", "nonsense"),
                           ("currency", "USD"), ("sku", "WRONG"),
                           ("unit_price", 1.5), ("approved_by", "  ")):
            with self.subTest(field=field, bad=bad):
                quote = copy.deepcopy(self.case["quotation"])
                quote["revision"] = 2
                quote[field] = bad
                with self.assertRaises(ValueError):
                    store.append_quotation_revision(quote)
        with closing(sqlite3.connect(self.path)) as db, db:
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("""INSERT INTO quotation_revision VALUES
                    ('Q-BAD', 1, 'RFQ-SYN-001', 'SKU', 'not a number', 'PCS',
                     'EUR', '12.50', '{}', 'DRAFT', NULL, '2026-10-03T00:00:00Z')""")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE opportunity SET status = 'GIBBERISH'")
        self.assertEqual(store.read_summary(self.case["opportunity_id"])["quotation_revisions"], 1)

    def test_outreach_requires_approval_for_exact_content(self):
        store = PilotStore(self.path)
        store.save_synthetic_case(self.case)
        oid = self.case["opportunity_id"]
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE opportunity SET status = 'OUTREACH_REVIEW' WHERE id = ?", (oid,))
            db.execute("INSERT INTO outreach VALUES (?, ?, ?, ?, ?, ?, ?)",
                       ("OUT-1", oid, f"C-{oid}", "hash-a", None, "REVIEW", "2026-10-03T00:00:00Z"))
        with self.assertRaisesRegex(ValueError, "outreach content approval"):
            store.transition(oid, OpportunityStatus.RFQ_RECEIVED)
        self.assertEqual(store.read_summary(oid)["status"], "OUTREACH_REVIEW")

    def test_backup_can_be_restored_as_a_new_database(self):
        store = PilotStore(self.path)
        store.save_synthetic_case(self.case)
        backup = Path(self.temp.name) / "backup.sqlite3"
        store.backup_to(backup)
        restored = PilotStore(backup)
        self.assertEqual(restored.read_summary(self.case["opportunity_id"])["order_total"], "1250.00")
        with self.assertRaisesRegex(ValueError, "destination must differ"):
            store.backup_to(self.path)

    def test_v1_schema_is_upgraded_to_v13(self):
        migration = Path(__file__).resolve().parents[1] / "pilot_engine" / "migrations" / "001_initial.sql"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript(migration.read_text(encoding="utf-8"))
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
        self.path.chmod(0o600)
        store = PilotStore(self.path)
        store.save_synthetic_case(self.case)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 18)

    def test_v1_upgrade_rejects_existing_invalid_quote(self):
        migration = Path(__file__).resolve().parents[1] / "pilot_engine" / "migrations" / "001_initial.sql"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            db.executescript(migration.read_text(encoding="utf-8"))
            db.execute("INSERT INTO manufacturer VALUES ('M', 'Synthetic', '2026-10-03T00:00:00Z')")
            db.execute("INSERT INTO product VALUES ('P', 'M', 'SKU', 'Clamp', 'PCS', '732690', NULL, 'UNVERIFIED', '2026-10-03T00:00:00Z', '2026-10-03T00:00:00Z')")
            db.execute("INSERT INTO market_target VALUES ('T', 'P', 'DE', '[]', '2026-10-03T00:00:00Z')")
            db.execute("INSERT INTO buyer_company VALUES ('B', 'Buyer', 'DE', 'BUYER', '2026-10-03T00:00:00Z')")
            db.execute("INSERT INTO opportunity VALUES ('O', 'P', 'T', 'B', 'Operator', 'DISCOVERED', 1, 'synthetic', 'O', '2026-10-03T00:00:00Z', '2026-10-03T00:00:00Z')")
            db.execute("INSERT INTO rfq VALUES ('R', 'O', 'R', '2026-10-03T00:00:00Z', '{}')")
            db.execute("INSERT INTO quotation_revision VALUES ('Q', 1, 'R', 'SKU', '2', 'PCS', 'EUR', '-1', '{}', 'APPROVED', 'Operator', '2026-10-03T00:00:00Z')")
        self.path.chmod(0o600)
        with self.assertRaises(sqlite3.IntegrityError):
            PilotStore(self.path)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_concurrent_v3_open_preserves_approval_and_migrates_once(self):
        store = PilotStore(self.path)
        store.save_synthetic_case(self.case)
        oid = self.case["opportunity_id"]
        store.record_approval(oid, "APPROVE_QUOTE", "Q-SYN-001", 1)
        with closing(sqlite3.connect(self.path)) as db, db:
            for table in ("sell_inbound_review", "sell_inbound_message", "sell_provider_observation"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_followup_event", "sell_followup_stop", "sell_followup"):
                db.execute(f"DROP TABLE {table}")
            for table in ("manual_contact_event", "manual_contact_action", "sell_opportunity"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_send_result", "sell_send_attempt", "sell_send_decision"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_draft_rejection", "sell_draft_revision", "sell_draft"):
                db.execute(f"DROP TABLE {table}")
            db.execute("DROP TABLE contact_suppression_rule")
            db.execute("DROP TABLE find_handoff_revision")
            db.execute("DROP TABLE find_handoff")
            db.execute("DROP TABLE contact_route_check")
            for table in ("contact_route_observation", "contact_route_correction", "discovered_contact_route",
                          "contact_route_absence", "contact_suppression", "contact_collection_policy"):
                db.execute(f"DROP TABLE {table}")
            db.execute("DROP TRIGGER profile_source_no_update")
            db.execute("DROP TRIGGER profile_source_no_delete")
            db.execute("DROP TABLE profile_source_document")
            db.execute("DROP TRIGGER buyer_fit_no_update")
            db.execute("DROP TRIGGER buyer_fit_no_delete")
            db.execute("DROP TABLE buyer_fit_decision")
            db.execute("DROP TRIGGER candidate_evidence_no_update")
            db.execute("DROP TRIGGER candidate_evidence_no_delete")
            db.execute("DROP TRIGGER candidate_no_delete")
            db.execute("DROP TABLE buyer_candidate_alias")
            db.execute("DROP TABLE buyer_candidate_evidence")
            db.execute("DROP TABLE buyer_candidate")
            db.execute("DROP TRIGGER market_signal_no_update")
            db.execute("DROP TRIGGER market_signal_no_delete")
            db.execute("DROP TABLE market_signal_snapshot")
            db.execute("DROP TRIGGER profile_new_opportunity_review")
            db.execute("DROP TABLE profile_revalidation")
            for name in ("profile_event_no_delete", "profile_event_no_update",
                         "profile_revision_no_delete", "profile_revision_no_update"):
                db.execute(f"DROP TRIGGER {name}")
            db.execute("DROP TABLE product_profile_event")
            db.execute("DROP TABLE product_profile_revision")
            db.execute("DROP TRIGGER approval_no_update")
            db.execute("DROP TRIGGER approval_no_delete")
            db.execute("PRAGMA user_version = 3")

        barrier = Barrier(4)

        def open_after_barrier(_):
            barrier.wait()
            return PilotStore(self.path).read_summary(oid)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(open_after_barrier, range(4)))
        self.assertTrue(all(result["id"] == oid for result in results))
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 18)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM approval").fetchone()[0], 1)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "approvals are append-only"):
                db.execute("DELETE FROM approval")

    def test_os_role_controls_contact_approval_and_records_denials(self):
        admin = PilotStore(self.path)
        admin.save_synthetic_case(self.case)
        uid = os.geteuid()
        viewer = PilotStore(self.path, access=LocalAccess({uid: Role.VIEWER}))
        oid = self.case["opportunity_id"]
        self.assertIsNotNone(viewer.read_summary(oid))
        with self.assertRaises(PermissionError):
            viewer.read_contact(f"C-{oid}")
        with self.assertRaises(PermissionError):
            viewer.record_approval(oid, "APPROVE_QUOTE", "Q-SYN-001", 1)
        with self.assertRaises(PermissionError):
            viewer.transition(oid, OpportunityStatus.DOCUMENT_REVIEW)
        with self.assertRaises(TypeError):
            admin.record_approval(oid, "APPROVE_QUOTE", "Q-SYN-001", 1, "Forged actor")
        contact = admin.read_contact(f"C-{oid}")
        self.assertEqual(contact["business_email"], "purchasing@example.com")
        with closing(sqlite3.connect(self.path)) as db, db:
            rows = db.execute("SELECT permission, allowed, actor_id FROM access_decision WHERE allowed = 0").fetchall()
            self.assertEqual({row[0] for row in rows}, {"READ_CONTACT", "APPROVE_QUOTE", "TRANSITION"})
            self.assertEqual({row[2] for row in rows}, {f"uid:{uid}"})
            self.assertNotIn("purchasing@example.com", str(rows))

    def test_fixture_operator_cannot_forge_authenticated_owner_or_audit_actor(self):
        forged = copy.deepcopy(self.case)
        forged["operator_id"] = "Forged Administrator"
        PilotStore(self.path).save_synthetic_case(forged)
        with closing(sqlite3.connect(self.path)) as db, db:
            owner = db.execute("SELECT owner_id FROM opportunity").fetchone()[0]
            actor = db.execute("SELECT actor_id FROM audit_event").fetchone()[0]
        self.assertEqual((owner, actor), (f"uid:{os.geteuid()}", f"uid:{os.geteuid()}"))

    def test_database_and_backup_are_owner_only(self):
        store = PilotStore(self.path)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        backup = Path(self.temp.name) / "private-backup.sqlite3"
        store.backup_to(backup)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.path.chmod(0o644)
        with self.assertRaises(PermissionError):
            PilotStore(self.path)


if __name__ == "__main__":
    unittest.main()
