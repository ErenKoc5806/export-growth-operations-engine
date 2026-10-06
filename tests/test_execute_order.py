import sqlite3
import unittest
from contextlib import closing

from pilot_engine.execute_order import ExecuteOrder
import test_quotations


class ExecuteOrderTests(unittest.TestCase):
    def setUp(self):
        test_quotations.QuotationTests.setUp(self)
        self.quote_id, _ = self.quotes.save(self.rfq_id, self.authority, **self.args)
        self.quotes.decide(self.quote_id, 1, "APPROVE",
                           expected_payload_sha256=self.quotes.read(self.quote_id)["payload_sha256"],
                           reason="Invented quote accepted for synthetic test")
        self.execute = ExecuteOrder(self.store)
        self.po = {"buyer": "Example Buyer", "seller": "Example Clamp Works",
                   "sku": "EXAMPLE-001", "description": "Connection clamp per example drawing",
                   "quantity": "150", "unit": "PCS", "unit_price": "12.50", "currency": "EUR",
                   "total": "1875.00", "incoterm_code": "FCA",
                   "incoterm_place": "Example city",
                   "requested_delivery_at_utc": "2029-11-01T00:00:00Z"}
        self.inputs = dict(source_ref="SYN-PO-1", original_ref="SYN-DOC-PO-1",
                           original=b"invented customer PO", received_at_utc="2026-10-06T12:00:00Z")

    def test_mismatch_blocks_approval_and_correction_creates_one_order(self):
        bad = {**self.po, "quantity": "99", "total": "1237.50"}
        po_id, rev = self.execute.save(self.quote_id, payload=bad, **self.inputs)
        view = self.execute.read(po_id)
        self.assertEqual(view["status"], "REVIEW_REQUIRED")
        self.assertIn("quantity", view["differences"])
        with self.assertRaisesRegex(ValueError, "mismatch"):
            self.execute.decide(po_id, rev, "APPROVE",
                                expected_payload_sha256=view["payload_sha256"], reason="Incorrect")
        self.execute.save(self.quote_id, payload=self.po, po_id=po_id,
                          expected_revision=rev, **self.inputs)
        view = self.execute.read(po_id)
        self.assertEqual(view["status"], "DRAFT")
        order_id = self.execute.decide(po_id, 2, "APPROVE",
                                       expected_payload_sha256=view["payload_sha256"],
                                       reason="Matched invented PO")
        self.assertEqual(order_id, self.execute.decide(
            po_id, 2, "APPROVE", expected_payload_sha256=view["payload_sha256"],
            reason="Repeated call"))
        self.assertEqual(self.execute.read_order(order_id)["status"], "LOCAL_ORDER_SYNTHETIC")
        self.assertFalse(self.execute.read_order(order_id)["erp_created"])
        with self.assertRaisesRegex(ValueError, "silently replaced"):
            self.execute.save(self.quote_id, payload=self.po, po_id=po_id,
                              expected_revision=2, **self.inputs)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM execute_local_order").fetchone()[0], 1)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("UPDATE execute_po_revision SET revision = 3")

    def test_quote_change_revocation_and_source_collision(self):
        po_id, _ = self.execute.save(self.quote_id, payload=self.po, **self.inputs)
        self.assertEqual(self.execute.save(self.quote_id, payload=self.po, **self.inputs), (po_id, 1))
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.execute.save(self.quote_id, payload=self.po,
                              **{**self.inputs, "original": b"different original"})
        self.quotes.decide(self.quote_id, 1, "REVOKE", expected_payload_sha256=None,
                           reason="Manufacturer withdrew invented quote")
        self.assertEqual(self.execute.read(po_id)["status"], "REVIEW_REQUIRED")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            self.execute.decide(po_id, 1, "APPROVE",
                                expected_payload_sha256=self.execute.read(po_id)["payload_sha256"],
                                reason="Stale quote")


if __name__ == "__main__":
    unittest.main()
