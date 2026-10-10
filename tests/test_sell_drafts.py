import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from pilot_engine.access import LocalAccess, Role
from pilot_engine.candidates import CandidateDiscovery
from pilot_engine.contact_routes import ContactRoutes
from pilot_engine.find_handoff import FindHandoff
from pilot_engine.profiles import ProductProfiles
from pilot_engine.qualification import BuyerQualification
from pilot_engine.sell_drafts import SellDrafts
from pilot_engine.store import PilotStore
from test_product_profiles import example_profile


class SellDraftTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "pilot.sqlite3"
        self.store = PilotStore(self.path)
        self.profiles = ProductProfiles(self.store)
        self.profiles.register_source_document("EXAMPLE-SOURCE-1", b"invented test document")
        self.product_id, _ = self.profiles.save_draft(example_profile())
        self.profiles.decide(self.product_id, 1, "APPROVED", "Invented test approval")
        self.candidates = CandidateDiscovery(self.store)
        candidate = self.candidates.record(
            source_system="EXAMPLE_SITE", source_ref="CANDIDATE-1", query="clamp",
            observed_at_utc="2026-10-04T00:00:00Z", name="Example Buyer",
            country_code="DE", role_hypothesis="DISTRIBUTOR", website="https://example.org",
            evidence_urls=["https://example.org/products"], summary="Invented distributor")
        self.candidate_id = candidate["id"]
        BuyerQualification(self.store).decide(
            self.product_id, self.candidate_id, 1, outcome="ACCEPT",
            checks={"product_spec": "CONFIRMED", "buyer_role": "CONFIRMED", "corridor": "CONFIRMED"},
            cited_evidence_ids=[candidate["evidence"][0]["id"]],
            explanation="Invented product and distributor fit reviewed")
        self.routes = ContactRoutes(self.store)
        self.routes.authorize_source(
            source_system="EXAMPLE_SITE", source_ref="CONTACT-1",
            source_url="https://example.org/contact", data_class="PERSONAL_ROUTE",
            decision="ALLOW", processing_basis="OTHER_REVIEWED",
            lawful_basis_ref="SYNTHETIC-BASIS", source_terms_ref="SYNTHETIC-TERMS",
            retention_until_utc="2030-01-01T00:00:00Z")
        route = self.routes.record(
            self.product_id, self.candidate_id, kind="GENERIC_EMAIL", value="sales@example.org",
            source_system="EXAMPLE_SITE", source_ref="CONTACT-1",
            source_url="https://example.org/contact", observed_at_utc="2026-10-04T00:00:00Z")
        self.route_id = route["id"]
        self.routes.check(self.route_id, method="MANUAL_PAGE", result="ROUTE_CONFIRMED",
                          source_url="https://example.org/contact",
                          checked_at_utc="2026-10-04T00:00:00Z", explanation="Invented page")
        self.handoff_id, _ = FindHandoff(self.store).record(
            self.product_id, self.candidate_id, self.route_id)
        self.drafts = SellDrafts(self.store)
        self.sender = dict(sender_name="Example Seller", sender_address="seller@example.com")

    def test_template_edit_reject_and_evidence(self):
        draft_id, revision = self.drafts.create(self.handoff_id, **self.sender,
                                                claim_refs=["EXAMPLE-DRAWING-1"])
        first = self.drafts.read(draft_id)
        self.assertEqual((revision, first["status"], first["channel"]), (1, "CURRENT_DRAFT", "EMAIL"))
        self.assertEqual(first["recipient_value"], "sales@example.org")
        self.assertEqual(first["sender_name"], "Example Seller")
        self.assertIn("Connection clamp", first["body"])
        self.assertEqual(first["buyer_fit"]["outcome"], "ACCEPT")
        self.assertEqual(len(first["candidate_evidence_ids"]), 1)
        self.assertEqual(first["permitted_claims"][0]["evidence_ref"], "EXAMPLE-DRAWING-1")
        self.assertIn("HUMAN_CLAIM_AND_RECIPIENT_REVIEW_REQUIRED", first["warnings"])
        self.assertFalse(first["send_allowed"])
        self.assertFalse(first["approval_valid"])
        self.drafts.reject(draft_id, 1, "Too generic")
        self.assertEqual(self.drafts.read(draft_id)["status"], "REJECTED")
        self.assertEqual(self.drafts.revise(
            draft_id, 1, **self.sender, language="en", subject="Specific question",
            body="Does this clamp suit your catalog?", claim_refs=[]), 2)
        self.assertEqual(self.drafts.read(draft_id)["status"], "CURRENT_DRAFT")
        self.assertEqual(self.drafts.read(draft_id)["source"], "OPERATOR_EDIT")
        self.assertIn("EDITED_TEXT_CLAIMS_UNVERIFIED", self.drafts.read(draft_id)["warnings"])
        self.assertEqual(self.drafts.read(draft_id, 1)["status"], "REJECTED")
        with self.assertRaisesRegex(ValueError, "revision changed"):
            self.drafts.revise(draft_id, 1, **self.sender, language="en",
                               subject="Old", body="Old", claim_refs=[])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 25)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("UPDATE sell_draft_revision SET body = 'forged' WHERE draft_id = ?",
                           (draft_id,))

    def test_staleness_suppression_and_unconfirmed_application(self):
        with self.assertRaisesRegex(ValueError, "supported"):
            self.drafts.create(self.handoff_id, **self.sender, claim_refs=["not-evidence"])
        draft_id, _ = self.drafts.create(self.handoff_id, **self.sender)
        self.drafts.revise(draft_id, 1, **self.sender, language="en", subject="Exhaust clamp",
                           body="Unconfirmed pipe clamp application", claim_refs=[])
        self.assertTrue(any("UNCONFIRMED_APPLICATION" in w
                            for w in self.drafts.read(draft_id)["warnings"]))
        self.routes.suppress("GENERIC_EMAIL", "sales@example.org", "Invented opt-out")
        stale = self.drafts.read(draft_id)
        self.assertEqual(stale["status"], "REVIEW_REQUIRED")
        self.assertIsNone(stale["recipient_value"])
        with self.assertRaisesRegex(ValueError, "Current Find handoff"):
            self.drafts.create(self.handoff_id, **self.sender)
        with self.assertRaisesRegex(ValueError, "Current Find handoff"):
            self.drafts.revise(draft_id, 2, **self.sender, language="en",
                               subject="Hello", body="Hello", claim_refs=[])

    def test_access_and_approval_boundary(self):
        viewer = SellDrafts(PilotStore(self.path, access=LocalAccess({os.geteuid(): Role.VIEWER})))
        with self.assertRaises(PermissionError):
            viewer.create(self.handoff_id, **self.sender)
        draft_id, _ = self.drafts.create(self.handoff_id, **self.sender)
        with self.assertRaises(PermissionError):
            viewer.read(draft_id)
        self.assertIsNone(self.drafts.read("missing"))
        with self.assertRaisesRegex(ValueError, "Email draft needs"):
            self.drafts.create(self.handoff_id, sender_name="Example", sender_address="not-email")


if __name__ == "__main__":
    unittest.main()
