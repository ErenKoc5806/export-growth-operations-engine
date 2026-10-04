import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from pilot_engine.access import LocalAccess, Role
from pilot_engine.candidates import CandidateDiscovery
from pilot_engine.profiles import ProductProfiles
from pilot_engine.qualification import BuyerQualification
from pilot_engine.store import PilotStore
from test_product_profiles import example_profile


class BuyerQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "review.sqlite3"
        self.store = PilotStore(self.path)
        self.profiles = ProductProfiles(self.store)
        self.candidates = CandidateDiscovery(self.store)
        self.review = BuyerQualification(self.store)
        self.product_id, _ = self.profiles.save_draft(example_profile())
        self.candidate = self.candidates.record(
            source_system="MANUAL_OFFICIAL_SITE", source_ref="SITE-1", query="clamp company Germany",
            observed_at_utc="2026-10-04T15:00:00Z", name="Example Distributor",
            country_code="DE", role_hypothesis="DISTRIBUTOR", website="https://example.org",
            evidence_urls=["https://example.org/clamps"],
            summary="Category page suggests a distributor, but no product fit is established")
        self.evidence_id = self.candidate["evidence"][0]["id"]
        self.checks = {"product_spec": "CONFIRMED", "buyer_role": "CONFIRMED",
                       "corridor": "CONFIRMED"}

    def decide(self, **changes):
        args = dict(outcome="ACCEPT", checks=self.checks, cited_evidence_ids=[self.evidence_id],
                    explanation="Operator compared exact specification and company evidence")
        args.update(changes)
        return self.review.decide(self.product_id, self.candidate["id"], 1, **args)

    def test_accept_requires_approved_current_profile_and_explicit_checks(self):
        with self.assertRaisesRegex(ValueError, "profile approval"):
            self.decide()
        self.profiles.decide(self.product_id, 1, "APPROVED", "example review")
        with self.assertRaisesRegex(ValueError, "must be confirmed"):
            self.decide(checks={**self.checks, "product_spec": "UNKNOWN"})
        with self.assertRaisesRegex(ValueError, "must belong"):
            self.decide(cited_evidence_ids=["CE-OTHER"])
        sequence = self.decide()
        result = self.review.status(self.product_id, self.candidate["id"])
        self.assertEqual((result["sequence"], result["status"]), (sequence, "QUALIFIED"))
        self.assertFalse(result["outreach_allowed"])
        with closing(sqlite3.connect(self.path)) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("UPDATE buyer_fit_decision SET outcome = 'REJECT'")

    def test_profile_change_or_new_evidence_invalidates_prior_acceptance(self):
        self.profiles.decide(self.product_id, 1, "APPROVED", "example review")
        self.decide()
        self.candidates.record(
            source_system="MANUAL_OFFICIAL_SITE", source_ref="SITE-2", query="another page",
            observed_at_utc="2026-10-04T16:00:00Z", name="Example Distributor",
            country_code="DE", role_hypothesis="DISTRIBUTOR", website="https://example.org/about",
            evidence_urls=["https://example.org/about"], summary="A second page adds context")
        self.assertEqual(self.review.status(self.product_id, self.candidate["id"])["status"],
                         "REVIEW_REQUIRED")
        new = example_profile()
        new["material"] = "Stainless steel"
        self.profiles.save_draft(new, product_id=self.product_id, expected_revision=1)
        self.assertEqual(self.review.status(self.product_id, self.candidate["id"])["status"],
                         "REVIEW_REQUIRED")

    def test_defer_and_reject_are_retained_without_fake_qualification(self):
        self.decide(outcome="DEFER", checks={**self.checks, "product_spec": "UNKNOWN"})
        self.assertEqual(self.review.status(self.product_id, self.candidate["id"])["status"], "DEFER")
        self.decide(outcome="REJECT", checks={**self.checks, "product_spec": "MISMATCH"})
        self.assertEqual(self.review.status(self.product_id, self.candidate["id"])["status"], "REJECT")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM buyer_fit_decision").fetchone()[0], 2)

    def test_viewer_cannot_record_qualification(self):
        viewer = BuyerQualification(PilotStore(
            self.path, access=LocalAccess({os.geteuid(): Role.VIEWER})))
        with self.assertRaises(PermissionError):
            viewer.decide(self.product_id, self.candidate["id"], 1,
                          outcome="DEFER", checks=self.checks,
                          cited_evidence_ids=[self.evidence_id],
                          explanation="Cannot review with a viewer account")


if __name__ == "__main__":
    unittest.main()
