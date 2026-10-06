import sqlite3
import unittest
from contextlib import closing

from pilot_engine.rfq import SellRFQ
from pilot_engine.store import PilotStore
import test_inbound


class SellRFQTests(unittest.TestCase):
    def setUp(self):
        test_inbound.InboundResponseTests.setUp(self)
        self.rfqs = SellRFQ(self.store)
        self.inbound_id = self.inbound.record_inbound(
            source_ref="SYN-RFQ-MSG-1", raw_ref="SYN-RAW-RFQ-1",
            received_at_utc="2026-10-06T02:00:00Z", channel="EMAIL")
        self.inbound.review(self.inbound_id, self.opportunity,
                            classification="RFQ_CANDIDATE", evidence_ref="SYN-RAW-RFQ-1",
                            explanation="Invented buyer asks for a priced product")
        self.complete = {"customer_reference": "SYN-CUST-RFQ-1", "sku": "EXAMPLE-001",
                         "specification": "Connection clamp per example drawing",
                         "quantity": "150", "unit": "PCS", "destination": "DE",
                         "requested_terms": {"incoterm": "FCA Example city"},
                         "response_due_at_utc": "2026-10-20T12:00:00Z",
                         "resolution_note": "Operator reviewed the synthetic request"}

    def test_incomplete_draft_revision_acceptance_and_revocation(self):
        rfq_id, rev = self.rfqs.save(self.inbound_id, {"sku": "EXAMPLE-001"})
        self.assertEqual(rev, 1)
        self.assertIn("quantity", self.rfqs.read(rfq_id)["missing_fields"])
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.rfqs.decide(rfq_id, 1, "ACCEPT", "Still missing quantity")
        self.assertEqual(self.rfqs.save(self.inbound_id, self.complete,
                                        rfq_id=rfq_id, expected_revision=1), (rfq_id, 2))
        self.assertEqual(self.rfqs.read(rfq_id)["status"], "DRAFT")
        self.rfqs.decide(rfq_id, 2, "ACCEPT", "Invented complete customer request")
        view = self.rfqs.read(rfq_id)
        self.assertEqual(view["status"], "ACCEPTED_SYNTHETIC")
        self.assertFalse(view["live_customer_request"])
        self.assertTrue(view["quote_allowed"])
        self.assertEqual(view["source_review"]["evidence_ref"], "SYN-RAW-RFQ-1")
        self.rfqs.decide(rfq_id, 2, "REVOKE", "Operator found an ambiguity")
        self.assertEqual(self.rfqs.read(rfq_id)["status"], "REVOKED")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 19)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("UPDATE sell_rfq_revision SET revision = 3")

    def test_interest_is_not_rfq_and_evidence_change_stales_acceptance(self):
        other = self.inbound.record_inbound(source_ref="SYN-INTEREST", raw_ref="SYN-RAW-INTEREST",
                                            received_at_utc="2026-10-06T02:30:00Z", channel="EMAIL")
        self.inbound.review(other, self.opportunity, classification="INTEREST",
                            evidence_ref="SYN-RAW-INTEREST", explanation="Just asked for a catalog")
        with self.assertRaisesRegex(ValueError, "RFQ request"):
            self.rfqs.save(other, self.complete)
        rfq_id, _ = self.rfqs.save(self.inbound_id, self.complete)
        self.rfqs.decide(rfq_id, 1, "ACCEPT", "Operator verified requirements")
        self.inbound.review(self.inbound_id, self.opportunity, classification="INTEREST",
                            evidence_ref="SYN-RAW-RFQ-1", explanation="Correction: just interest")
        self.assertEqual(self.rfqs.read(rfq_id)["status"], "REVIEW_REQUIRED")
        self.assertFalse(self.rfqs.read(rfq_id)["quote_allowed"])
        with self.assertRaisesRegex(ValueError, "RFQ request"):
            self.rfqs.decide(rfq_id, 1, "ACCEPT", "Stale source")

    def test_mismatch_and_invalid_quantity_block(self):
        with self.assertRaisesRegex(ValueError, "quantity"):
            self.rfqs.save(self.inbound_id, {**self.complete, "quantity": "-1"})
        rfq_id, _ = self.rfqs.save(self.inbound_id, {**self.complete, "sku": "OTHER"})
        with self.assertRaisesRegex(ValueError, "resolution"):
            self.rfqs.decide(rfq_id, 1, "ACCEPT", "Product differs")
        with self.assertRaisesRegex(ValueError, "revision changed"):
            self.rfqs.save(self.inbound_id, self.complete, rfq_id=rfq_id, expected_revision=0)

    def test_revoked_product_blocks_accepted_rfq_for_quotation(self):
        rfq_id, _ = self.rfqs.save(self.inbound_id, self.complete)
        self.rfqs.decide(rfq_id, 1, "ACCEPT", "Synthetic requirements reviewed")
        self.profiles.decide(self.product_id, 1, "REVOKED", "Withdraw invented product")
        self.assertEqual(self.rfqs.read(rfq_id)["status"], "REVIEW_REQUIRED")
        self.assertFalse(self.rfqs.read(rfq_id)["quote_allowed"])

    def test_v18_upgrade_keeps_inbound(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            for table in ("sell_rfq_decision", "sell_rfq_revision", "sell_rfq"):
                db.execute(f"DROP TABLE {table}")
            db.execute("PRAGMA user_version = 18")
        reopened = PilotStore(self.path)
        self.assertIsNotNone(self.inbound.read(self.inbound_id))
        self.assertTrue(SellRFQ(reopened).save(self.inbound_id, self.complete)[0].startswith("RF-"))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 19)


if __name__ == "__main__":
    unittest.main()
