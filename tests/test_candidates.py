import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from pilot_engine.access import LocalAccess, Role
from pilot_engine.candidates import CandidateDiscovery
from pilot_engine.store import PilotStore


class CandidateDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "candidate.sqlite3"
        self.candidates = CandidateDiscovery(PilotStore(self.path))
        self.input = {
            "source_system": "MANUAL_OFFICIAL_SITE", "source_ref": "RUN-1:1",
            "query": "Germany connection clamp distributor", "observed_at_utc": "2026-10-04T15:00:00Z",
            "name": "Example Parts GmbH", "country_code": "DE",
            "role_hypothesis": "DISTRIBUTOR", "website": "https://www.example.org/",
            "evidence_urls": ["https://www.example.org/clamps"],
            "summary": "Site lists clamp products; exact manufacturer SKU is unknown",
        }

    def test_source_backed_candidate_and_idempotent_replay(self):
        first = self.candidates.record(**self.input)
        replay = self.candidates.record(**self.input)
        self.assertEqual(first["id"], replay["id"])
        self.assertEqual(len(first["evidence"]), 1)
        self.assertEqual(first["qualification_status"], "UNQUALIFIED")
        self.assertFalse(first["product_fit_verified"])
        self.assertFalse(first["contact_verified"])
        self.assertFalse(first["outreach_allowed"])
        changed = dict(self.input, summary="Different source content")
        with self.assertRaisesRegex(ValueError, "different evidence"):
            self.candidates.record(**changed)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM buyer_candidate").fetchone()[0], 1)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("DELETE FROM buyer_candidate_evidence")

    def test_domain_alias_and_name_duplicate_are_reviewable(self):
        first = self.candidates.record(**self.input)
        variant = dict(self.input, source_ref="RUN-2:1", name="Example Parts Deutschland",
                       website="https://example.org/about", evidence_urls=["https://example.org/about"])
        same = self.candidates.record(**variant)
        self.assertEqual(same["id"], first["id"])
        self.assertIn("Example Parts Deutschland", same["aliases"])
        self.assertEqual(len(same["evidence"]), 2)
        no_domain = dict(self.input, source_ref="RUN-3:1", website=None,
                         evidence_urls=["https://directory.example.net/example-parts"])
        flagged = self.candidates.record(**no_domain)
        self.assertNotEqual(flagged["id"], first["id"])
        self.assertEqual(flagged["possible_duplicate_of"], first["id"])

    def test_ti_mapping_keeps_candidate_unqualified_and_rejects_wrong_country(self):
        ti = {"name": "Example Parts GmbH", "country": "Germany",
              "website": "https://example.org", "role": "buyer",
              "relevance_summary": "Broad clamp category, product fit unknown",
              "evidence_urls": ["https://example.org/clamps"], "confidence": 0.99}
        result = self.candidates.import_ti_candidate(
            ti, source_ref="TI-RUN-1:1", query="clamps Germany",
            observed_at_utc="2026-10-04T15:00:00Z")
        self.assertFalse(result["product_fit_verified"])
        self.assertFalse(result["outreach_allowed"])
        ti["country"] = "Austria"
        with self.assertRaisesRegex(ValueError, "Germany"):
            self.candidates.import_ti_candidate(
                ti, source_ref="TI-RUN-1:2", query="clamps Germany",
                observed_at_utc="2026-10-04T15:00:00Z")

    def test_host_variants_and_related_subdomain_are_visible(self):
        first = self.candidates.record(**self.input)
        trailing = dict(self.input, source_ref="RUN-DOT", website="https://example.org./",
                        evidence_urls=["https://example.org./clamps"])
        self.assertEqual(self.candidates.record(**trailing)["id"], first["id"])
        sub = dict(self.input, source_ref="RUN-SUB", name="Example Shop",
                   website="https://shop.example.org/", evidence_urls=["https://shop.example.org/clamps"])
        separate = self.candidates.record(**sub)
        self.assertNotEqual(separate["id"], first["id"])
        self.assertIn(first["id"], separate["possible_duplicates"])
        self.assertIn(separate["id"], self.candidates.read(first["id"])["possible_duplicates"])

    def test_bad_source_and_viewer_denial(self):
        for wrong in (dict(self.input, evidence_urls=[]),
                      dict(self.input, evidence_urls=["http://example.org"]),
                      dict(self.input, country_code="FR")):
            with self.assertRaises(ValueError):
                self.candidates.record(**wrong)
        viewer = CandidateDiscovery(PilotStore(
            self.path, access=LocalAccess({os.geteuid(): Role.VIEWER})))
        with self.assertRaises(PermissionError):
            viewer.record(**self.input)

    def test_v6_upgrade_retains_market_signal_table(self):
        with closing(sqlite3.connect(self.path)) as db, db:
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
            db.execute("PRAGMA user_version = 6")
        PilotStore(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 13)
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name = 'market_signal_snapshot'").fetchone())


if __name__ == "__main__":
    unittest.main()
