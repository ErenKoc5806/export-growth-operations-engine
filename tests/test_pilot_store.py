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
        store.transition(oid, OpportunityStatus.CONTACT_REVIEW, "Operator")
        summary = PilotStore(self.path).read_summary(oid)
        self.assertEqual((summary["status"], summary["revision"]), ("CONTACT_REVIEW", 2))
        self.assertNotIn("business_email", summary)
        with sqlite3.connect(self.path) as db:
            audit = db.execute("SELECT action, before_state, after_state FROM audit_event ORDER BY id").fetchall()
            self.assertEqual(len(audit), 2)
            self.assertEqual(audit[-1], ("STATUS_CHANGED", "DISCOVERED", "CONTACT_REVIEW"))
            self.assertEqual(db.execute("SELECT business_email FROM contact_evidence").fetchone()[0], "purchasing@example.com")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM audit_event")

    def test_backup_can_be_restored_as_a_new_database(self):
        store = PilotStore(self.path)
        store.save_synthetic_case(self.case)
        backup = Path(self.temp.name) / "backup.sqlite3"
        store.backup_to(backup)
        restored = PilotStore(backup)
        self.assertEqual(restored.read_summary(self.case["opportunity_id"])["order_total"], "1250.00")
        with self.assertRaisesRegex(ValueError, "destination must differ"):
            store.backup_to(self.path)


if __name__ == "__main__":
    unittest.main()
