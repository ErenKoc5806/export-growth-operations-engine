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
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 15)

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
        self.routes.correct_identity(saved["id"], person_name="Another Person",
                                     person_role="Purchasing", reason="Second review confirmed identity")
        with closing(sqlite3.connect(self.path)) as db:
            first_event = db.execute("""SELECT id FROM contact_route_correction
                WHERE route_id = ? ORDER BY rowid LIMIT 1""", (saved["id"],)).fetchone()[0]
            with self.assertRaisesRegex(sqlite3.IntegrityError, "mutation needs correction"):
                db.execute("""UPDATE discovered_contact_route SET person_name = 'Forged',
                    last_correction_id = ? WHERE id = ?""", (first_event, saved["id"]))
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

    def test_plus_alias_and_company_domain_suppression(self):
        self.qualify()
        self.policy()
        base = self.routes.record(self.product_id, self.candidate_id,
                                  **{**self.kwargs, "value": "buyer@example.org"})
        alias = self.routes.record(self.product_id, self.candidate_id,
                                   **{**self.kwargs, "value": "buyer+trade@example.org"})
        self.routes.suppress("GENERIC_EMAIL", "buyer+trade@example.org", "Synthetic opt-out")
        self.assertEqual(self.routes.read(base["id"])["status"], "SUPPRESSED")
        self.assertEqual(self.routes.read(alias["id"])["status"], "SUPPRESSED")
        with self.assertRaisesRegex(ValueError, "suppressed"):
            self.routes.record(self.product_id, self.candidate_id,
                               **{**self.kwargs, "value": "buyer+new@example.org"})
        other = self.routes.record(self.product_id, self.candidate_id,
                                   **{**self.kwargs, "value": "office@example.org"})
        self.routes.suppress_domain("example.org", "Company-wide synthetic opt-out")
        self.assertEqual(self.routes.read(other["id"])["status"], "SUPPRESSED")
        with self.assertRaisesRegex(ValueError, "suppressed"):
            self.routes.record(self.product_id, self.candidate_id,
                               **{**self.kwargs, "value": "new@example.org"})
        sub = self.candidates.record(
            source_system="EXAMPLE_SITE", source_ref="SUB-CANDIDATE", query="example shop",
            observed_at_utc="2026-10-04T01:00:00Z", name="Example Sub Shop",
            country_code="DE", role_hypothesis="DISTRIBUTOR",
            website="https://shop.example.org", evidence_urls=["https://shop.example.org/products"],
            summary="Invented separate subdomain company candidate")
        BuyerQualification(self.store).decide(
            self.product_id, sub["id"], 1, outcome="ACCEPT",
            checks={"product_spec": "CONFIRMED", "buyer_role": "CONFIRMED", "corridor": "CONFIRMED"},
            cited_evidence_ids=[sub["evidence"][0]["id"]],
            explanation="Invented product and subdomain company reviewed in synthetic test")
        with self.assertRaisesRegex(ValueError, "suppressed"):
            self.routes.record(self.product_id, sub["id"],
                               **{**self.kwargs, "value": "new@shop.example.org"})
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contact_suppression_rule").fetchone()[0], 2)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("DELETE FROM contact_suppression_rule")

    def test_form_normalization_and_direct_sql_mutation_guard(self):
        self.qualify()
        self.policy(source_ref="FORM-1", data_class="BUSINESS_ROUTE")
        form = dict(self.kwargs, source_ref="FORM-1", kind="CONTACT_FORM",
                    value="https://EXAMPLE.ORG/contact/form/")
        first = self.routes.record(self.product_id, self.candidate_id, **form)
        second = self.routes.record(self.product_id, self.candidate_id,
                                    **{**form, "value": "https://example.org/contact/form"})
        self.assertEqual(first["id"], second["id"])
        variant = self.routes.record(self.product_id, self.candidate_id,
                                     **{**form, "value": "https://example.org/contact/form?ref=trade"})
        self.assertIn(first["id"], variant["possible_duplicates"])
        with closing(sqlite3.connect(self.path)) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "mutation needs correction"):
                db.execute("UPDATE discovered_contact_route SET route_value = 'https://evil.example' WHERE id = ?",
                           (first["id"],))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "mutation needs correction"):
                db.execute("UPDATE discovered_contact_route SET source_ref = 'forged' WHERE id = ?",
                           (first["id"],))

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

    def test_route_check_stales_on_new_evidence_and_bounce_blocks(self):
        self.qualify()
        self.policy()
        route = self.routes.record(self.product_id, self.candidate_id, **self.kwargs)
        check = dict(method="MANUAL_PAGE", result="ROUTE_CONFIRMED",
                     source_url="https://example.org/contact", checked_at_utc="2026-10-04T00:00:00Z",
                     explanation="Operator compared route with invented company page")
        self.routes.check(route["id"], **check)
        self.assertEqual(self.routes.read(route["id"])["status"], "VERIFIED_ROUTE")
        self.assertFalse(self.routes.read(route["id"])["outreach_allowed"])
        self.policy(source_ref="CONTACT-2", source_url="https://example.org/about")
        self.routes.record(self.product_id, self.candidate_id, **{
            **self.kwargs, "source_ref": "CONTACT-2", "source_url": "https://example.org/about",
            "observed_at_utc": "2026-10-04T01:00:00Z"})
        self.assertEqual(self.routes.read(route["id"])["status"], "REVIEW_REQUIRED")
        self.routes.check(route["id"], **{**check, "source_url": "https://example.org/about",
                                           "checked_at_utc": "2026-10-04T01:00:00Z"})
        self.assertEqual(self.routes.read(route["id"])["status"], "VERIFIED_ROUTE")
        self.routes.check(route["id"], method="PROVIDER_FEEDBACK", result="BOUNCED",
                          source_url="https://example.org/about", checked_at_utc="2026-10-04T02:00:00Z",
                          explanation="Invented provider bounce in test")
        self.assertEqual(self.routes.read(route["id"])["status"], "BOUNCED")
        with closing(sqlite3.connect(self.path)) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("DELETE FROM contact_route_check")

    def test_domain_mismatch_and_role_uncertainty_require_review(self):
        self.qualify()
        self.policy()
        route = self.routes.record(self.product_id, self.candidate_id,
                                   **{**self.kwargs, "value": "buyer@other.example"})
        args = dict(method="MANUAL_PAGE", result="ROUTE_CONFIRMED",
                    source_url="https://example.org/contact", checked_at_utc="2026-10-04T00:00:00Z",
                    explanation="External mailbox requires a documented company-domain review")
        with self.assertRaisesRegex(ValueError, "domain mismatch"):
            self.routes.check(route["id"], **args)
        self.routes.check(route["id"], **{**args, "domain_review_ref": "SYNTHETIC-REVIEW"})
        self.assertEqual(self.routes.read(route["id"])["status"], "VERIFIED_ROUTE")
        named = self.routes.record(self.product_id, self.candidate_id,
                                   **{**self.kwargs, "kind": "NAMED_EMAIL",
                                      "value": "person@example.org", "person_name": "Example Person"})
        with self.assertRaisesRegex(ValueError, "role uncertainty"):
            self.routes.check(named["id"], **args)
        with self.assertRaisesRegex(ValueError, "recorded source"):
            self.routes.check(route["id"], **{**args, "source_url": "https://example.org/unknown"})

    def test_policy_change_requires_new_observation_before_reverification(self):
        self.qualify()
        self.policy()
        route = self.routes.record(self.product_id, self.candidate_id, **self.kwargs)
        check = dict(method="MANUAL_PAGE", result="ROUTE_CONFIRMED",
                     source_url="https://example.org/contact", checked_at_utc="2026-10-04T00:00:00Z",
                     explanation="Invented official page observation")
        self.routes.check(route["id"], **check)
        self.policy(decision="REVOKE")
        self.assertEqual(self.routes.read(route["id"])["status"], "REVIEW_REQUIRED")
        self.policy()
        self.assertEqual(self.routes.read(route["id"])["status"], "REVIEW_REQUIRED")
        with self.assertRaisesRegex(ValueError, "source-use"):
            self.routes.check(route["id"], **check)

    def test_old_check_and_check_before_observation_are_not_current(self):
        self.qualify()
        self.policy()
        route = self.routes.record(self.product_id, self.candidate_id, **{
            **self.kwargs, "observed_at_utc": "2025-01-01T00:00:00Z"})
        check = dict(method="MANUAL_PAGE", result="ROUTE_CONFIRMED",
                     source_url="https://example.org/contact", checked_at_utc="2025-01-02T00:00:00Z",
                     explanation="Old invented observation")
        with self.assertRaisesRegex(ValueError, "predate"):
            self.routes.check(route["id"], **{**check, "checked_at_utc": "2024-12-31T00:00:00Z"})
        self.routes.check(route["id"], **check)
        self.assertEqual(self.routes.read(route["id"])["status"], "REVIEW_REQUIRED")

    def test_v9_upgrade_preserves_prior_profile_and_candidate(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            for table in ("sell_send_result", "sell_send_attempt", "sell_send_decision"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_draft_rejection", "sell_draft_revision", "sell_draft"):
                db.execute(f"DROP TABLE {table}")
            db.execute("DROP TABLE contact_suppression_rule")
            db.execute("DROP TABLE find_handoff_revision")
            db.execute("DROP TABLE find_handoff")
            db.execute("DROP TABLE contact_route_check")
            for table in ("contact_route_observation", "contact_route_correction",
                          "discovered_contact_route", "contact_route_absence",
                          "contact_suppression", "contact_collection_policy"):
                db.execute(f"DROP TABLE {table}")
            db.execute("PRAGMA user_version = 9")
        reopened = PilotStore(self.path)
        self.assertIsNotNone(ProductProfiles(reopened).read(self.product_id))
        self.assertIsNotNone(CandidateDiscovery(reopened).read(self.candidate_id))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 15)

    def test_v10_upgrade_preserves_contact_route(self):
        self.qualify()
        self.policy()
        route = self.routes.record(self.product_id, self.candidate_id, **self.kwargs)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DROP TRIGGER contact_route_guard_update")
            db.execute("ALTER TABLE discovered_contact_route DROP COLUMN last_correction_id")
            for table in ("sell_send_result", "sell_send_attempt", "sell_send_decision"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_draft_rejection", "sell_draft_revision", "sell_draft"):
                db.execute(f"DROP TABLE {table}")
            db.execute("DROP TABLE contact_suppression_rule")
            db.execute("DROP TABLE find_handoff_revision")
            db.execute("DROP TABLE find_handoff")
            db.execute("DROP TABLE contact_route_check")
            db.execute("PRAGMA user_version = 10")
        reopened = ContactRoutes(PilotStore(self.path))
        self.assertEqual(reopened.read(route["id"])["route_value"], "sales@example.org")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 15)

    def test_handoff_versions_current_evidence_without_duplicate_identity(self):
        self.qualify()
        self.policy()
        route = self.routes.record(self.product_id, self.candidate_id, **self.kwargs)
        self.routes.check(route["id"], method="MANUAL_PAGE", result="ROUTE_CONFIRMED",
                          source_url="https://example.org/contact", checked_at_utc="2026-10-04T00:00:00Z",
                          explanation="Synthetic route on company page")
        handoffs = FindHandoff(self.store)
        with self.assertRaisesRegex(ValueError, "Real Find handoff"):
            handoffs.record(self.product_id, self.candidate_id, route["id"], data_origin="REAL")
        handoff_id, rev = handoffs.record(self.product_id, self.candidate_id, route["id"])
        self.assertEqual(rev, 1)
        self.assertEqual(handoffs.record(self.product_id, self.candidate_id, route["id"]),
                         (handoff_id, 1))
        self.assertEqual(handoffs.read(handoff_id)["status"], "CURRENT_RESEARCH")
        self.assertEqual(handoffs.read(handoff_id)["market_country"], "DE")
        self.assertTrue(handoffs.read(handoff_id)["manufacturer_id"].startswith("M-"))
        self.assertFalse(handoffs.read(handoff_id)["send_allowed"])
        self.policy(source_ref="CONTACT-2", source_url="https://example.org/about")
        self.routes.record(self.product_id, self.candidate_id, **{
            **self.kwargs, "source_ref": "CONTACT-2", "source_url": "https://example.org/about",
            "observed_at_utc": "2026-10-04T01:00:00Z"})
        self.assertEqual(handoffs.read(handoff_id)["status"], "REVIEW_REQUIRED")
        with self.assertRaisesRegex(ValueError, "verified route"):
            handoffs.record(self.product_id, self.candidate_id, route["id"])
        self.routes.check(route["id"], method="MANUAL_PAGE", result="ROUTE_CONFIRMED",
                          source_url="https://example.org/about", checked_at_utc="2026-10-04T01:00:00Z",
                          explanation="Rechecked new synthetic observation")
        self.assertEqual(handoffs.record(self.product_id, self.candidate_id, route["id"]),
                         (handoff_id, 2))
        self.assertEqual(handoffs.read(handoff_id)["status"], "CURRENT_RESEARCH")
        self.routes.suppress("GENERIC_EMAIL", "sales@example.org", "Synthetic opt-out")
        self.assertEqual(handoffs.read(handoff_id)["status"], "REVIEW_REQUIRED")
        self.assertIsNone(handoffs.read(handoff_id)["route_value"])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM find_handoff").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM find_handoff_revision").fetchone()[0], 2)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("DELETE FROM find_handoff_revision")

    def test_v11_upgrade_preserves_contact_check(self):
        self.qualify()
        self.policy()
        route = self.routes.record(self.product_id, self.candidate_id, **self.kwargs)
        self.routes.check(route["id"], method="MANUAL_PAGE", result="ROUTE_CONFIRMED",
                          source_url="https://example.org/contact", checked_at_utc="2026-10-04T00:00:00Z",
                          explanation="Synthetic route on company page")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DROP TRIGGER contact_route_guard_update")
            db.execute("ALTER TABLE discovered_contact_route DROP COLUMN last_correction_id")
            for table in ("sell_send_result", "sell_send_attempt", "sell_send_decision"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_draft_rejection", "sell_draft_revision", "sell_draft"):
                db.execute(f"DROP TABLE {table}")
            db.execute("DROP TABLE contact_suppression_rule")
            db.execute("DROP TABLE find_handoff_revision")
            db.execute("DROP TABLE find_handoff")
            db.execute("PRAGMA user_version = 11")
        reopened = ContactRoutes(PilotStore(self.path))
        self.assertEqual(reopened.read(route["id"])["status"], "VERIFIED_ROUTE")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 15)

    def test_v12_upgrade_preserves_route_and_installs_mutation_guard(self):
        self.qualify()
        self.policy()
        route = self.routes.record(self.product_id, self.candidate_id, **self.kwargs)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DROP TRIGGER contact_route_guard_update")
            db.execute("ALTER TABLE discovered_contact_route DROP COLUMN last_correction_id")
            for table in ("sell_send_result", "sell_send_attempt", "sell_send_decision"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_draft_rejection", "sell_draft_revision", "sell_draft"):
                db.execute(f"DROP TABLE {table}")
            db.execute("DROP TABLE contact_suppression_rule")
            db.execute("PRAGMA user_version = 12")
        reopened = ContactRoutes(PilotStore(self.path))
        self.assertEqual(reopened.read(route["id"])["route_value"], "sales@example.org")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 15)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "mutation needs correction"):
                db.execute("UPDATE discovered_contact_route SET person_name = 'forged' WHERE id = ?",
                           (route["id"],))


if __name__ == "__main__":
    unittest.main()
