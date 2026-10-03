import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

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
        with sqlite3.connect(self.path) as db:
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
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM buyer_company").fetchone()[0], 1)

    def test_transition_and_audit_are_atomic_and_contact_is_separate(self):
        store = PilotStore(self.path)
        store.save_synthetic_case(self.case)
        oid = self.case["opportunity_id"]
        with self.assertRaisesRegex(ValueError, "Illegal"):
            store.transition(oid, OpportunityStatus.ORDER_DRAFT, "Operator")
        summary = PilotStore(self.path).read_summary(oid)
        self.assertEqual((summary["status"], summary["revision"]), ("SYNTHETIC_DRAFT", 1))
        self.assertNotIn("business_email", summary)
        with sqlite3.connect(self.path) as db:
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
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE opportunity SET status = 'QUOTE_REVIEW' WHERE id = ?", (oid,))
        with self.assertRaisesRegex(ValueError, "[Qq]uotation revision approval"):
            store.transition(oid, OpportunityStatus.QUOTE_APPROVED, "Synthetic Operator")
        self.assertEqual(store.read_summary(oid)["status"], "QUOTE_REVIEW")
        with self.assertRaisesRegex(ValueError, "do not match"):
            store.record_approval(oid, "APPROVE_QUOTE", "Q-SYN-001", 1, "Wrong actor")
        store.record_approval(oid, "APPROVE_QUOTE", "Q-SYN-001", 1, "Synthetic Operator")
        with self.assertRaisesRegex(ValueError, "quotation revision approval"):
            store.transition(oid, OpportunityStatus.QUOTE_APPROVED, "Synthetic Operator",
                             approval_target_id="Q-SYN-001", approval_revision=99)
        store.transition(oid, OpportunityStatus.QUOTE_APPROVED, "Synthetic Operator",
                         approval_target_id="Q-SYN-001", approval_revision=1)
        store.transition(oid, OpportunityStatus.PO_REVIEW, "Synthetic Operator")
        with self.assertRaisesRegex(ValueError, "customer PO approval"):
            store.transition(oid, OpportunityStatus.ORDER_DRAFT, "Synthetic Operator",
                             approval_target_id="PO-SYN-001", approval_revision=1)
        store.record_approval(oid, "APPROVE_PO", "PO-SYN-001", 1, "Synthetic Operator")
        store.transition(oid, OpportunityStatus.ORDER_DRAFT, "Synthetic Operator",
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
        with sqlite3.connect(self.path) as db:
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
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE opportunity SET status = 'OUTREACH_REVIEW' WHERE id = ?", (oid,))
            db.execute("INSERT INTO outreach VALUES (?, ?, ?, ?, ?, ?, ?)",
                       ("OUT-1", oid, f"C-{oid}", "hash-a", None, "REVIEW", "2026-10-03T00:00:00Z"))
        with self.assertRaisesRegex(ValueError, "outreach content approval"):
            store.transition(oid, OpportunityStatus.RFQ_RECEIVED, "Synthetic Operator")
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

    def test_v1_schema_is_upgraded_to_v2(self):
        migration = Path(__file__).resolve().parents[1] / "pilot_engine" / "migrations" / "001_initial.sql"
        with sqlite3.connect(self.path) as db:
            db.executescript(migration.read_text(encoding="utf-8"))
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
        store = PilotStore(self.path)
        store.save_synthetic_case(self.case)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_v1_upgrade_rejects_existing_invalid_quote(self):
        migration = Path(__file__).resolve().parents[1] / "pilot_engine" / "migrations" / "001_initial.sql"
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys = ON")
            db.executescript(migration.read_text(encoding="utf-8"))
            db.execute("INSERT INTO manufacturer VALUES ('M', 'Synthetic', '2026-10-03T00:00:00Z')")
            db.execute("INSERT INTO product VALUES ('P', 'M', 'SKU', 'Clamp', 'PCS', '732690', NULL, 'UNVERIFIED', '2026-10-03T00:00:00Z', '2026-10-03T00:00:00Z')")
            db.execute("INSERT INTO market_target VALUES ('T', 'P', 'DE', '[]', '2026-10-03T00:00:00Z')")
            db.execute("INSERT INTO buyer_company VALUES ('B', 'Buyer', 'DE', 'BUYER', '2026-10-03T00:00:00Z')")
            db.execute("INSERT INTO opportunity VALUES ('O', 'P', 'T', 'B', 'Operator', 'DISCOVERED', 1, 'synthetic', 'O', '2026-10-03T00:00:00Z', '2026-10-03T00:00:00Z')")
            db.execute("INSERT INTO rfq VALUES ('R', 'O', 'R', '2026-10-03T00:00:00Z', '{}')")
            db.execute("INSERT INTO quotation_revision VALUES ('Q', 1, 'R', 'SKU', '2', 'PCS', 'EUR', '-1', '{}', 'APPROVED', 'Operator', '2026-10-03T00:00:00Z')")
        with self.assertRaises(sqlite3.IntegrityError):
            PilotStore(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
