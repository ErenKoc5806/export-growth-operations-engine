import copy
import hashlib
import json
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
        "source_sha256": hashlib.sha256(b"invented test document").hexdigest(),
        "minimum_order_quantity": "100", "lead_time_days": 14,
        "payment_terms": "Example advance payment", "incoterm_code": "FCA",
        "incoterm_place": "Example city",
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
        self.profiles.register_source_document("EXAMPLE-SOURCE-1", b"invented test document")

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
        with self.assertRaises(PermissionError):
            viewer.save_draft({"manufacturer_name": "", "product_name": ""})
        with closing(sqlite3.connect(self.path)) as db:
            denied = db.execute("""SELECT COUNT(*) FROM access_decision
                WHERE permission = 'EDIT_PROFILE' AND allowed = 0""").fetchone()[0]
            self.assertEqual(denied, 2)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 15)
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
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 15)

    def test_manufacturer_reused_and_document_digest_is_bound(self):
        original = example_profile()
        original["manufacturer_legal_id"] = "TR-111"
        first, _ = self.profiles.save_draft(original)
        second_payload = example_profile()
        second_payload.update(product_name="Other clamp", sku="EXAMPLE-002",
                              manufacturer_legal_id="TR-111", manufacturer_name="EXAMPLE CLAMP WORKS")
        second, _ = self.profiles.save_draft(second_payload)
        with closing(sqlite3.connect(self.path)) as db:
            ids = [db.execute("SELECT manufacturer_id FROM product WHERE id = ?", (p,)).fetchone()[0]
                   for p in (first, second)]
            self.assertEqual(ids[0], ids[1])
        wrong = example_profile()
        wrong["source_sha256"] = "0" * 64
        wrong["manufacturer_legal_id"] = "TR-111"
        third, _ = self.profiles.save_draft(wrong)
        with self.assertRaisesRegex(ValueError, "source document hash"):
            self.profiles.decide(third, 1, "APPROVED", "unmatched")
        with self.assertRaisesRegex(ValueError, "already bound"):
            self.profiles.register_source_document("EXAMPLE-SOURCE-1", b"changed original")
        self.profiles.decide(second, 1, "APPROVED", "synthetic example")
        with closing(sqlite3.connect(self.path)) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("UPDATE profile_source_document SET sha256 = 'wrong'")

    def test_legal_id_separates_same_names_and_name_only_requires_selection(self):
        unverified, _ = self.profiles.save_draft(example_profile())
        first = example_profile()
        first["manufacturer_legal_id"] = "TR-111"
        first_product, _ = self.profiles.save_draft(first)
        second = example_profile()
        second["manufacturer_legal_id"] = "TR-222"
        second_product, _ = self.profiles.save_draft(second)
        ids = [self.profiles.read(pid)["manufacturer_id"] for pid in
               (unverified, first_product, second_product)]
        self.assertEqual(len(set(ids)), 3)
        with self.assertRaisesRegex(ValueError, "Name-only manufacturer match"):
            self.profiles.save_draft(example_profile())
        linked, _ = self.profiles.save_draft(example_profile(), manufacturer_id=ids[0])
        self.assertEqual(self.profiles.read(linked)["manufacturer_id"], ids[0])
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.profiles.save_draft(second, manufacturer_id=ids[1])
        with self.assertRaisesRegex(ValueError, "different manufacturer"):
            self.profiles.save_draft(first, manufacturer_id=ids[0])
        conflicting_revision = example_profile()
        conflicting_revision["manufacturer_legal_id"] = "TR-222"
        with self.assertRaisesRegex(ValueError, "Legal ID belongs to a different"):
            self.profiles.save_draft(conflicting_revision, product_id=unverified, expected_revision=1)

    def test_turkish_i_variants_are_name_hints_only(self):
        first = example_profile()
        first["manufacturer_name"] = "KIRIKKALE CLAMP"
        product_id, _ = self.profiles.save_draft(first)
        second = example_profile()
        second["manufacturer_name"] = "Kırıkkale Clamp"
        with self.assertRaisesRegex(ValueError, "Name-only manufacturer match"):
            self.profiles.save_draft(second)
        another, _ = self.profiles.save_draft(
            second, manufacturer_id=self.profiles.read(product_id)["manufacturer_id"])
        self.assertEqual(self.profiles.read(another)["manufacturer_id"],
                         self.profiles.read(product_id)["manufacturer_id"])
        third = example_profile()
        third["manufacturer_name"] = "KİRİKKALE CLAMP"
        with self.assertRaisesRegex(ValueError, "Name-only manufacturer match"):
            self.profiles.save_draft(third)

    def test_commercial_inputs_and_legacy_approval_require_new_revision(self):
        invalid = example_profile()
        invalid["minimum_order_quantity"] = "0"
        with self.assertRaisesRegex(ValueError, "minimum_order_quantity"):
            self.profiles.save_draft(invalid)
        invalid = example_profile()
        invalid["incoterm_code"] = "INVALID"
        with self.assertRaisesRegex(ValueError, "Incoterms"):
            self.profiles.save_draft(invalid)
        product_id, _ = self.profiles.save_draft(example_profile())
        self.profiles.decide(product_id, 1, "APPROVED", "example review")
        # Simulate an approval created before v9's new evidence requirements.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DROP TRIGGER profile_revision_no_update")
            old = example_profile()
            old.pop("source_sha256")
            old.pop("minimum_order_quantity")
            old.pop("lead_time_days")
            old.pop("payment_terms")
            old.pop("incoterm_code")
            old.pop("incoterm_place")
            legacy_json = json.dumps(old, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            db.execute("""UPDATE product_profile_revision SET payload_json = ?, payload_sha256 = ?
                WHERE product_id = ?""", (legacy_json, hashlib.sha256(legacy_json.encode()).hexdigest(),
                                           product_id))
        self.assertFalse(self.profiles.read(product_id)["approved"])
        with self.assertRaisesRegex(ValueError, "Current manufacturer"):
            self.profiles.require_current_approval(product_id, 1)

    def test_revoked_profile_can_close_open_opportunity(self):
        product_id, _ = self.profiles.save_draft(example_profile())
        self.profiles.decide(product_id, 1, "APPROVED", "example review")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO market_target VALUES ('T-1', ?, 'DE', '[]', 'now')", (product_id,))
            db.execute("INSERT INTO buyer_company VALUES ('B-1', 'Example Buyer', 'DE', 'BUYER', 'now')")
            db.execute("""INSERT INTO opportunity VALUES
                ('O-CLOSE', ?, 'T-1', 'B-1', 'test', 'DISCOVERED', 1, 'test', 'O-CLOSE', 'now', 'now')""",
                (product_id,))
        self.profiles.decide(product_id, 1, "REVOKED", "product withdrawn")
        with self.assertRaisesRegex(ValueError, "not approved"):
            self.profiles.acknowledge_revalidation("O-CLOSE", 1, "cannot re-review")
        self.store.transition("O-CLOSE", OpportunityStatus.CLOSED)
        self.assertEqual(self.store.read_summary("O-CLOSE")["status"], "CLOSED")


if __name__ == "__main__":
    unittest.main()
