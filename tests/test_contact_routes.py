import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from pilot_engine.access import LocalAccess, Role
from pilot_engine.candidates import CandidateDiscovery
from pilot_engine.contact_routes import ContactRoutes
from pilot_engine.profiles import ProductProfiles
from pilot_engine.qualification import BuyerQualification
from pilot_engine.store import PilotStore
from test_product_profiles import example_profile


class ContactRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "contact.sqlite3"
        self.store = PilotStore(self.path)
        self.routes = ContactRoutes(self.store)
        self.profiles = ProductProfiles(self.store)
        self.profiles.register_source_document("EXAMPLE-SOURCE-1", b"invented test document")
        self.product_id, _ = self.profiles.save_draft(example_profile())
        self.candidates = CandidateDiscovery(self.store)
        self.company = self.candidates.record(
            source_system="EXAMPLE_SITE", source_ref="CANDIDATE-1", query="example clamps",
            observed_at_utc="2026-10-04T00:00:00Z", name="Example Buyer",
            country_code="DE", role_hypothesis="DISTRIBUTOR", website="https://example.org",
            evidence_urls=["https://example.org/products"], summary="Invented distributor page")
        self.candidate_id = self.company["id"]
        self.kwargs = dict(kind="GENERIC_EMAIL", value="sales@example.org",
                           source_system="EXAMPLE_SITE", source_ref="CONTACT-1",
                           source_url="https://example.org/contact",
                           observed_at_utc="2026-10-04T00:00:00Z")

    def qualify(self):
        self.profiles.decide(self.product_id, 1, "APPROVED", "synthetic example")
        BuyerQualification(self.store).decide(
            self.product_id, self.candidate_id, 1, outcome="ACCEPT",
            checks={"product_spec": "CONFIRMED", "buyer_role": "CONFIRMED", "corridor": "CONFIRMED"},
            cited_evidence_ids=[self.company["evidence"][0]["id"]],
            explanation="Invented product and distributor reviewed in synthetic test")

    def policy(self, **changes):
        args = dict(source_system="EXAMPLE_SITE", source_ref="CONTACT-1",
                    source_url="https://example.org/contact", data_class="PERSONAL_ROUTE",
                    decision="ALLOW", processing_basis="OTHER_REVIEWED",
                    lawful_basis_ref="SYNTHETIC-BASIS-1", source_terms_ref="SYNTHETIC-TERMS-1",
                    retention_until_utc="2030-01-01T00:00:00Z")
        args.update(changes)
        return self.routes.authorize_source(**args)

    def test_qualification_and_source_policy_gate_collection(self):
        with self.assertRaisesRegex(ValueError, "qualified"):
            self.routes.record(self.product_id, self.candidate_id, **self.kwargs)
        self.qualify()
        with self.assertRaisesRegex(ValueError, "collection"):
            self.routes.record(self.product_id, self.candidate_id, **self.kwargs)
        self.policy(data_class="BUSINESS_ROUTE")
        with self.assertRaisesRegex(ValueError, "collection"):
            self.routes.record(self.product_id, self.candidate_id, **self.kwargs)
        self.policy()
        route = self.routes.record(self.product_id, self.candidate_id, **self.kwargs)
        self.assertEqual(route["status"], "UNVERIFIED")
        self.assertFalse(route["outreach_allowed"])
        self.assertEqual(route["observations"][0]["source_ref"], "CONTACT-1")
        replay = self.routes.record(self.product_id, self.candidate_id, **self.kwargs)
        self.assertEqual(replay["id"], route["id"])
        self.assertEqual(len(replay["observations"]), 1)
        self.policy(source_ref="FORM-1", source_url="https://example.org/contact",
                    data_class="BUSINESS_ROUTE")
        form = self.routes.record(self.product_id, self.candidate_id,
                                  **{**self.kwargs, "kind": "CONTACT_FORM",
                                     "value": "https://example.org/contact/form",
                                     "source_ref": "FORM-1"})
        self.assertEqual(form["status"], "UNVERIFIED")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 10)

    def test_named_route_suppression_and_conflicting_evidence(self):
        self.qualify()
        self.policy(data_class="PERSONAL_ROUTE")
        named = dict(self.kwargs, kind="NAMED_EMAIL", value="Person@example.org",
                     person_name="Example Person", person_role="Purchasing")
        saved = self.routes.record(self.product_id, self.candidate_id, **named)
        with self.assertRaisesRegex(ValueError, "Conflicting contact"):
            self.routes.record(self.product_id, self.candidate_id,
                               **{**named, "person_name": "Another Person"})
        corrected = self.routes.correct_identity(
            saved["id"], person_name="Another Person", person_role="Purchasing",
            reason="Operator checked the source again")
        self.assertEqual(corrected["person_name"], "Another Person")
        self.assertEqual(len(corrected["corrections"]), 1)
        self.policy(source_ref="CONTACT-2", source_url="https://example.org/team",
                    data_class="PERSONAL_ROUTE")
        updated = self.routes.record(self.product_id, self.candidate_id, **{
            **named, "person_name": "Another Person", "source_ref": "CONTACT-2",
            "source_url": "https://example.org/team", "observed_at_utc": "2026-10-04T01:00:00Z"})
        self.assertEqual(len(updated["observations"]), 2)
        self.assertEqual(updated["observations"][-1]["source_url"], "https://example.org/team")
        with self.assertRaisesRegex(ValueError, "Generic route"):
            self.routes.record(self.product_id, self.candidate_id,
                               **{**named, "kind": "GENERIC_EMAIL"})
        self.routes.suppress("GENERIC_EMAIL", "person@EXAMPLE.org", "Requested no contact")
        redacted = self.routes.read(saved["id"])
        self.assertEqual(redacted["status"], "SUPPRESSED")
        self.assertIsNone(redacted["route_value"])
        self.assertIsNone(redacted["person_name"])
        self.assertIsNone(redacted["observations"][0]["source_url"])
        with self.assertRaisesRegex(ValueError, "suppressed"):
            self.routes.record(self.product_id, self.candidate_id, **named)
        with closing(sqlite3.connect(self.path)) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("DELETE FROM contact_suppression")

    def test_revoke_expire_and_stale_fit_fail_closed(self):
        self.qualify()
        self.policy()
        prior = self.routes.record(self.product_id, self.candidate_id, **self.kwargs)
        self.policy(decision="REVOKE")
        self.assertEqual(self.routes.read(prior["id"])["status"], "REVIEW_REQUIRED")
        with self.assertRaisesRegex(ValueError, "collection"):
            self.routes.record(self.product_id, self.candidate_id, **self.kwargs)
        with self.assertRaisesRegex(ValueError, "future"):
            self.policy(retention_until_utc="2020-01-01T00:00:00Z")
        self.policy()
        self.candidates.record(
            source_system="EXAMPLE_SITE", source_ref="CANDIDATE-2", query="example clamps",
            observed_at_utc="2026-10-04T01:00:00Z", name="Example Buyer",
            country_code="DE", role_hypothesis="DISTRIBUTOR", website="https://example.org/about",
            evidence_urls=["https://example.org/about"], summary="New invented company evidence")
        with self.assertRaisesRegex(ValueError, "qualified"):
            self.routes.record(self.product_id, self.candidate_id, **self.kwargs)

    def test_missing_route_and_access_boundary(self):
        self.qualify()
        missing = self.routes.record_missing(
            self.product_id, self.candidate_id, source_url="https://example.org/contact",
            observed_at_utc="2026-10-04T00:00:00Z", explanation="No route on invented contact page")
        self.assertTrue(missing.startswith("CM-"))
        self.assertEqual(self.routes.record_missing(
            self.product_id, self.candidate_id, source_url="https://example.org/contact",
            observed_at_utc="2026-10-04T00:00:00Z", explanation="No route on invented contact page"),
            missing)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.routes.record_missing(
                self.product_id, self.candidate_id, source_url="https://example.org/contact",
                observed_at_utc="2026-10-04T00:00:00Z", explanation="Different observation")
        self.assertEqual(self.routes.list_for_candidate(self.product_id, self.candidate_id)
                         ["missing"][0]["id"], missing)
        viewer = ContactRoutes(PilotStore(self.path, access=LocalAccess({os.geteuid(): Role.VIEWER})))
        with self.assertRaises(PermissionError):
            viewer.authorize_source(source_system="EXAMPLE_SITE", source_ref="CONTACT-1",
                                    source_url="https://example.org/contact", data_class="PERSONAL_ROUTE",
                                    decision="ALLOW", processing_basis="CONSENT", lawful_basis_ref="TEST",
                                    source_terms_ref="TEST", retention_until_utc="2030-01-01T00:00:00Z")
        with self.assertRaises(PermissionError):
            viewer.record(self.product_id, self.candidate_id, **self.kwargs)

    def test_v9_upgrade_preserves_prior_profile_and_candidate(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            for table in ("contact_route_observation", "contact_route_correction",
                          "discovered_contact_route", "contact_route_absence",
                          "contact_suppression", "contact_collection_policy"):
                db.execute(f"DROP TABLE {table}")
            db.execute("PRAGMA user_version = 9")
        reopened = PilotStore(self.path)
        self.assertIsNotNone(ProductProfiles(reopened).read(self.product_id))
        self.assertIsNotNone(CandidateDiscovery(reopened).read(self.candidate_id))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 10)


if __name__ == "__main__":
    unittest.main()
