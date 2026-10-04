import copy
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from pilot_engine.access import LocalAccess, Role
from pilot_engine.domain import OpportunityStatus
from pilot_engine.profiles import ProductProfiles
from pilot_engine.store import PilotStore


def example_profile():
    # Entirely invented data; the test actor's approval is not a manufacturer authorization.
    return {
        "manufacturer_name": "Example Clamp Works", "manufacturer_country": "TR",
        "product_name": "Connection clamp", "sku": "EXAMPLE-001", "unit": "PCS",
        "drawing_ref": "EXAMPLE-DRAWING-1", "dimensions": "20 x 30 mm",
        "material": "Steel", "intended_use": "General mechanical connection",
        "technical_limits": "Example only", "target_country": "DE", "buyer_role": "Distributor",
        "hs6": "732690", "classification_status": "REVIEWED",
        "classification_note": "Example research filter; actual tariff classification needs review",
        "source_kind": "MANUFACTURER", "source_ref": "EXAMPLE-SOURCE-1",
        "claims": [{"text": "Connection clamp", "confirmed_use": True,
                    "evidence_ref": "EXAMPLE-DRAWING-1"}],
        "search_terms": [{"text": "connection clamp", "confirmed_use": True,
                          "evidence_ref": "EXAMPLE-DRAWING-1"},
                         {"text": "exhaust clamp", "confirmed_use": False, "evidence_ref": ""},
                         {"text": "pipe clamp", "confirmed_use": False, "evidence_ref": ""}],
    }


class ProductProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "pilot.sqlite3"
        self.store = PilotStore(self.path)
        self.profiles = ProductProfiles(self.store)

    def test_draft_approval_and_terms_are_bound_to_current_revision(self):
        draft = {"manufacturer_name": "Example Clamp Works", "product_name": "Connection clamp",
                 "source_kind": "SYNTHETIC", "search_terms": [
                     {"text": "exhaust clamp", "confirmed_use": False, "evidence_ref": ""}]}
        product_id, revision = self.profiles.save_draft(draft)
        self.assertEqual((revision, self.profiles.read(product_id)["approved_search_terms"]), (1, []))
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.profiles.decide(product_id, 1, "APPROVED", "reviewed")
        complete = example_profile()
        _, revision = self.profiles.save_draft(complete, product_id=product_id, expected_revision=1)
        self.profiles.decide(product_id, revision, "APPROVED", "example technical review")
        view = self.profiles.require_current_approval(product_id, 2)
        self.assertEqual(view["approved_search_terms"], ["connection clamp"])
        self.assertNotIn("pipe clamp", view["approved_search_terms"])
        with self.assertRaisesRegex(ValueError, "revision changed"):
            self.profiles.save_draft(complete, product_id=product_id, expected_revision=1)
        changed = copy.deepcopy(complete)
        changed["material"] = "Stainless steel"
        self.profiles.save_draft(changed, product_id=product_id, expected_revision=2)
        with self.assertRaisesRegex(ValueError, "Current manufacturer"):
            self.profiles.require_current_approval(product_id, 2)
        with self.assertRaisesRegex(ValueError, "current"):
            self.profiles.decide(product_id, 2, "APPROVED", "stale")
        self.assertFalse(self.profiles.read(product_id)["approved"])

    def test_unconfirmed_claim_and_synthetic_source_block_approval(self):
        payload = example_profile()
        payload["claims"].append({"text": "exhaust clamp", "confirmed_use": False,
                                  "evidence_ref": ""})
        product_id, _ = self.profiles.save_draft(payload)
        with self.assertRaisesRegex(ValueError, "Unconfirmed product claims"):
            self.profiles.decide(product_id, 1, "APPROVED", "reviewed")
        payload["claims"].pop()
        payload["source_kind"] = "SYNTHETIC"
        self.profiles.save_draft(payload, product_id=product_id, expected_revision=1)
        with self.assertRaisesRegex(ValueError, "manufacturer-sourced"):
            self.profiles.decide(product_id, 2, "APPROVED", "reviewed")

    def test_revision_requires_downstream_revalidation_and_decisions_are_immutable(self):
        product_id, _ = self.profiles.save_draft(example_profile())
        self.profiles.decide(product_id, 1, "APPROVED", "initial example review")
        with closing(sqlite3.connect(self.path)) as db, db:
            manufacturer_id = db.execute("SELECT manufacturer_id FROM product WHERE id = ?",
                                         (product_id,)).fetchone()[0]
            self.assertTrue(manufacturer_id.startswith("M-"))
            db.execute("INSERT INTO market_target VALUES ('T-1', ?, 'DE', '[]', 'now')", (product_id,))
            db.execute("INSERT INTO buyer_company VALUES ('B-1', 'Example Buyer', 'DE', 'BUYER', 'now')")
            db.execute("""INSERT INTO opportunity VALUES
                ('O-1', ?, 'T-1', 'B-1', 'test', 'DISCOVERED', 1, 'test', 'O-1', 'now', 'now')""",
                (product_id,))
        with self.assertRaisesRegex(ValueError, "re-review"):
            self.store.transition("O-1", OpportunityStatus.CONTACT_REVIEW)
        self.profiles.acknowledge_revalidation("O-1", 1, "Initial buyer fit reviewed")
        self.store.transition("O-1", OpportunityStatus.CONTACT_REVIEW)
        revised = example_profile()
        revised["intended_use"] = "Revised general mechanical connection"
        self.profiles.save_draft(revised, product_id=product_id, expected_revision=1)
        with self.assertRaisesRegex(ValueError, "re-review"):
            self.store.transition("O-1", OpportunityStatus.CONTACT_READY)
        self.profiles.decide(product_id, 2, "APPROVED", "new product information")
        self.profiles.acknowledge_revalidation("O-1", 2, "Buyer fit and outreach reviewed")
        self.store.transition("O-1", OpportunityStatus.CONTACT_READY)
        self.profiles.decide(product_id, 2, "REVOKED", "material corrected again")
        with self.assertRaisesRegex(ValueError, "Current manufacturer"):
            self.profiles.require_current_approval(product_id, 2)
        with self.assertRaisesRegex(ValueError, "re-review"):
            self.store.transition("O-1", OpportunityStatus.OUTREACH_REVIEW)
        self.profiles.decide(product_id, 2, "APPROVED", "reapproved after clarification")
        self.assertTrue(self.profiles.require_current_approval(product_id, 2)["approved"])
        with self.assertRaisesRegex(ValueError, "re-review"):
            self.store.transition("O-1", OpportunityStatus.OUTREACH_REVIEW)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM product_profile_revision").fetchone()[0], 2)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("UPDATE product_profile_revision SET payload_json = '{}' WHERE revision = 1")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("DELETE FROM product_profile_event")

    def test_access_and_v4_upgrade(self):
        viewer = ProductProfiles(PilotStore(self.path, access=LocalAccess({os.geteuid(): Role.VIEWER})))
        with self.assertRaises(PermissionError):
            viewer.save_draft(example_profile())
        product_id, _ = self.profiles.save_draft(example_profile())
        with self.assertRaises(PermissionError):
            viewer.read(product_id)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 7)
            db.execute("DROP TRIGGER candidate_evidence_no_update")
            db.execute("DROP TRIGGER candidate_evidence_no_delete")
            db.execute("DROP TRIGGER candidate_no_delete")
            db.execute("DROP TABLE buyer_candidate_alias")
            db.execute("DROP TABLE buyer_candidate_evidence")
            db.execute("DROP TABLE buyer_candidate")
            db.execute("DROP TRIGGER market_signal_no_update")
            db.execute("DROP TRIGGER market_signal_no_delete")
            db.execute("DROP TABLE market_signal_snapshot")
            db.execute("DROP TABLE profile_revalidation")
            db.execute("DROP TRIGGER profile_new_opportunity_review")
            db.execute("DROP TRIGGER profile_event_no_delete")
            db.execute("DROP TRIGGER profile_event_no_update")
            db.execute("DROP TRIGGER profile_revision_no_delete")
            db.execute("DROP TRIGGER profile_revision_no_update")
            db.execute("DROP TABLE product_profile_event")
            db.execute("DROP TABLE product_profile_revision")
            db.execute("PRAGMA user_version = 4")
        PilotStore(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 7)


if __name__ == "__main__":
    unittest.main()
