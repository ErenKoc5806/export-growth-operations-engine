import copy
import sqlite3
import unittest
from contextlib import closing

from pilot_engine.quotations import Quotations
from pilot_engine.store import PilotStore
import test_rfq
from test_product_profiles import example_profile


class QuotationTests(unittest.TestCase):
    def setUp(self):
        test_rfq.SellRFQTests.setUp(self)
        self.rfq_id, _ = self.rfqs.save(self.inbound_id, self.complete)
        self.rfqs.decide(self.rfq_id, 1, "ACCEPT", "Synthetic product requirements reviewed")
        self.quotes = Quotations(self.store)
        self.authority = self.quotes.register_price_authority(
            self.product_id, source_ref="SYN-PRICE-1", content=b"invented pricing document",
            sku="EXAMPLE-001", currency="EUR", unit_price_text="12.50",
            valid_until_utc="2030-01-01T00:00:00Z")
        self.args = dict(valid_until_utc="2029-12-01T00:00:00Z", exclusions="Synthetic offer only")

    def test_authorized_price_and_exact_revision_approval(self):
        quote_id, rev = self.quotes.save(self.rfq_id, self.authority, **self.args)
        self.assertEqual(rev, 1)
        quote = self.quotes.read(quote_id)
        self.assertEqual(quote["payload"]["total"], "1875.00")
        self.assertEqual(quote["payload"]["incoterm_code"], "FCA")
        self.assertEqual(quote["status"], "DRAFT")
        with self.assertRaisesRegex(ValueError, "exact"):
            self.quotes.decide(quote_id, 1, "APPROVE", expected_payload_sha256="wrong",
                               reason="Wrong reviewed content")
        self.quotes.decide(quote_id, 1, "APPROVE",
                           expected_payload_sha256=quote["payload_sha256"],
                           reason="Reviewed synthetic pricing evidence")
        approved = self.quotes.read(quote_id)
        self.assertEqual(approved["status"], "APPROVED_SYNTHETIC")
        self.assertFalse(approved["send_allowed"])
        self.assertFalse(approved["customer_accepted"])
        self.assertFalse(approved["live_commercial_offer"])
        self.assertEqual(self.quotes.save(self.rfq_id, self.authority,
                                          quotation_id=quote_id, expected_revision=1,
                                          **{**self.args, "exclusions": "Revised synthetic exclusions"}),
                         (quote_id, 2))
        self.assertEqual(self.quotes.read(quote_id)["status"], "DRAFT")
        with self.assertRaisesRegex(ValueError, "Current quotation revision"):
            self.quotes.decide(quote_id, 1, "APPROVE",
                               expected_payload_sha256=quote["payload_sha256"],
                               reason="Old revision")
        self.quotes.decide(quote_id, 2, "APPROVE",
                           expected_payload_sha256=self.quotes.read(quote_id)["payload_sha256"],
                           reason="Reviewed revision two")
        self.quotes.decide(quote_id, 2, "REVOKE", expected_payload_sha256=None,
                           reason="Withdraw synthetic quote")
        self.assertEqual(self.quotes.read(quote_id)["status"], "REVOKED")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 26)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("UPDATE sell_quotation_revision SET revision = 3")

    def test_authority_scope_and_rfq_change_gate(self):
        self.assertEqual(self.quotes.register_price_authority(
            self.product_id, source_ref="SYN-PRICE-1", content=b"invented pricing document",
            sku="EXAMPLE-001", currency="EUR", unit_price_text="12.50",
            valid_until_utc="2030-01-01T00:00:00Z"), self.authority)
        with self.assertRaisesRegex(ValueError, "other terms"):
            self.quotes.register_price_authority(
                self.product_id, source_ref="SYN-PRICE-1", content=b"different bytes",
                sku="EXAMPLE-001", currency="EUR", unit_price_text="12.50",
                valid_until_utc="2030-01-01T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "price authority"):
            self.quotes.save(self.rfq_id, "not-registered", **self.args)
        with self.assertRaisesRegex(ValueError, "scope"):
            self.quotes.register_price_authority(
                self.product_id, source_ref="SYN-PRICE-2", content=b"invented",
                sku="EXAMPLE-001", currency="USD", unit_price_text="12.50",
                valid_until_utc="2030-01-01T00:00:00Z")
        quote_id, _ = self.quotes.save(self.rfq_id, self.authority, **self.args)
        self.inbound.review(self.inbound_id, self.opportunity, classification="INTEREST",
                            evidence_ref="SYN-RAW-RFQ-1", explanation="Correction after draft")
        self.assertEqual(self.quotes.read(quote_id)["status"], "REVIEW_REQUIRED")
        with self.assertRaisesRegex(ValueError, "accepted synthetic RFQ"):
            self.quotes.save(self.rfq_id, self.authority,
                             quotation_id=quote_id, expected_revision=1, **self.args)

    def test_v19_upgrade_preserves_rfq(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            for table in ("sell_quotation_decision", "sell_quotation_revision",
                          "sell_quotation", "sell_price_authority"):
                db.execute(f"DROP TABLE {table}")
            db.execute("PRAGMA user_version = 19")
        reopened = PilotStore(self.path)
        self.assertEqual(self.rfqs.read(self.rfq_id)["status"], "ACCEPTED_SYNTHETIC")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 26)
        self.assertIsNotNone(Quotations(reopened))

    def test_commercial_profile_change_invalidates_quotation(self):
        quote_id, _ = self.quotes.save(self.rfq_id, self.authority, **self.args)
        self.quotes.decide(quote_id, 1, "APPROVE",
                           expected_payload_sha256=self.quotes.read(quote_id)["payload_sha256"],
                           reason="Invented reviewed quote")
        revised = copy.deepcopy(example_profile())
        revised["payment_terms"] = "Invented revised advance terms"
        self.profiles.save_draft(revised, product_id=self.product_id, expected_revision=1)
        self.profiles.decide(self.product_id, 2, "APPROVED", "Invented commercial revision")
        self.assertEqual(self.quotes.read(quote_id)["status"], "REVIEW_REQUIRED")
        self.assertFalse(self.quotes.read(quote_id)["send_allowed"])


if __name__ == "__main__":
    unittest.main()
