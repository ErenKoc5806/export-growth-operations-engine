import sqlite3
import unittest
from contextlib import closing

from pilot_engine.execute_documents import ExecuteDocuments
from pilot_engine.shipments import ExecuteShipments
import test_execute_operations


class ShipmentTests(unittest.TestCase):
    def setUp(self):
        test_execute_operations.ExecuteOperationsTests.setUp(self)
        self.shipments = ExecuteShipments(self.store)
        self.docs = ExecuteDocuments(self.store)
        self.operations.readiness(self.order_id, operation_key="ready-60", status="PARTIAL",
            planned_at_utc="2026-11-01T00:00:00Z", actual_at_utc="2026-11-02T00:00:00Z",
            quantity="60", source_ref="SYN-MFG", reason="First batch", operator_confirmed=True)
        self.first = self.shipments.create(self.order_id, operation_key="ship-1",
                                           quantity="60", source_ref="SYN-PART-1")
        self.plan = dict(pickup="Example factory", delivery="Example city", packages=1,
            net_weight_kg="40", gross_weight_kg="50", dimensions="40 x 30 x 20 cm",
            requested_at_utc="2026-11-03T00:00:00Z")
        self.packages = [{"quantity": "60", "net_weight_kg": "40", "gross_weight_kg": "50",
                          "dimensions": "40 x 30 x 20 cm"}]

    def test_two_partial_shipments_reconcile_and_invoice_independently(self):
        self.assertEqual(self.shipments.create(self.order_id, operation_key="ship-1",
            quantity="60", source_ref="SYN-PART-1"), self.first)
        with self.assertRaisesRegex(ValueError, "insufficient"):
            self.shipments.create(self.order_id, operation_key="ship-2", quantity="90",
                                  source_ref="SYN-PART-2")
        self.operations.readiness(self.order_id, operation_key="ready-150", status="READY",
            planned_at_utc="2026-11-01T00:00:00Z", actual_at_utc="2026-11-03T00:00:00Z",
            quantity="150", source_ref="SYN-MFG-2", reason="Second batch", operator_confirmed=True)
        second = self.shipments.create(self.order_id, operation_key="ship-2", quantity="90",
                                       source_ref="SYN-PART-2")
        with self.assertRaisesRegex(ValueError, "exceeds"):
            self.shipments.create(self.order_id, operation_key="ship-3", quantity="1",
                                  source_ref="SYN-PART-3")
        self.assertEqual(self.shipments.summary(self.order_id)["unallocated_quantity"], "0")
        with self.assertRaisesRegex(ValueError, "shipment-specific"):
            self.operations.plan_freight(self.order_id, operation_key="legacy", status="REQUESTED",
                **self.plan)
        with self.assertRaisesRegex(ValueError, "shipment-specific"):
            self.docs.invoice(self.order_id, exporter_legal_id=None, buyer_legal_id=None,
                              tax_review_ref=None)
        self.docs.review_export_facts(self.order_id, hs_code="732690",
            classification_evidence_ref="SYN-HS", origin_country_code="TR",
            origin_evidence_ref="SYN-ORIGIN", manufacturer_review_ref="SYN-MFG")
        for sid, number, quantity, net, gross in ((self.first, 1, "60", "40", "50"),
                                                   (second, 2, "90", "60", "70")):
            plan = {**self.plan, "net_weight_kg": net, "gross_weight_kg": gross}
            self.shipments.plan_freight(sid, operation_key=f"freight-{number}", **plan)
            packages = [{**self.packages[0], "quantity": quantity,
                         "net_weight_kg": net, "gross_weight_kg": gross}]
            pid, _ = self.shipments.packing(sid, packages=packages, marks=f"SYN-{number}")
            packing = self.shipments.read_document(pid)
            self.shipments.review_document(pid, 1, decision="REVIEW",
                expected_payload_sha256=packing["payload_sha256"], reason="Reviewed evidence")
            iid, _ = self.shipments.invoice(sid, exporter_legal_id="SYN-TR",
                buyer_legal_id="SYN-DE", tax_review_ref="SYN-TAX",
                destination_review_ref="SYN-DEST")
            invoice = self.shipments.read_document(iid)
            self.assertEqual(invoice["payload"]["total"], "750.00" if number == 1 else "1125.00")
            self.assertEqual(invoice["payload"]["quantity"], quantity)
            self.shipments.review_document(iid, 1, decision="REVIEW",
                expected_payload_sha256=invoice["payload_sha256"], reason="Reviewed invoice")
            self.assertEqual(self.shipments.read_document(iid)["status"], "REVIEWED_SYNTHETIC")
        self.assertEqual(self.shipments.summary(self.order_id)["real_shipped_quantity"], "0")

    def test_stale_packing_and_duplicate_booking_are_blocked(self):
        self.shipments.plan_freight(self.first, operation_key="freight-a", **self.plan)
        pid, _ = self.shipments.packing(self.first, packages=self.packages, marks="SYN-BOX")
        packing = self.shipments.read_document(pid)
        self.shipments.review_document(pid, 1, decision="REVIEW",
            expected_payload_sha256=packing["payload_sha256"], reason="Reviewed")
        self.shipments.plan_freight(self.first, operation_key="freight-b", **self.plan)
        self.assertEqual(self.shipments.read_document(pid)["status"], "REVIEW_REQUIRED")
        with self.assertRaisesRegex(ValueError, "packing"):
            self.shipments.confirm_booking(self.first, operation_key="bad",
                confirmation_ref="SYN-CARRIER", source_ref="SYN-EVIDENCE",
                confirmed_at_utc="2026-11-04T00:00:00Z")
        self.shipments.packing(self.first, packages=self.packages, marks="SYN-BOX-2",
                               document_id=pid, expected_revision=1)
        packing = self.shipments.read_document(pid)
        self.shipments.review_document(pid, 2, decision="REVIEW",
            expected_payload_sha256=packing["payload_sha256"], reason="New freight")
        args = dict(operation_key="booking-unknown", confirmation_ref="SYN-CARRIER",
                    source_ref="SYN-EVIDENCE", confirmed_at_utc="2026-11-04T00:00:00Z",
                    outcome="UNKNOWN")
        self.assertEqual(self.shipments.confirm_booking(self.first, **args), self.first)
        self.assertEqual(self.shipments.confirm_booking(self.first, **args), self.first)
        with self.assertRaisesRegex(ValueError, "already observed"):
            self.shipments.confirm_booking(self.first, **{**args, "operation_key": "booking-2"})
        self.assertFalse(self.shipments.read(self.first)["booking"]["payload"]["real_carrier_booking"])
        self.shipments.plan_freight(self.first, operation_key="freight-c", **self.plan)
        self.assertEqual(self.shipments.read(self.first)["booking"]["status"], "REVIEW_REQUIRED")
        with self.assertRaisesRegex(ValueError, "already observed"):
            self.shipments.confirm_booking(self.first, **{**args, "operation_key": "retry"})
        with closing(sqlite3.connect(self.path)) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("DELETE FROM execute_shipment")

    def test_readiness_and_event_key_conflicts(self):
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.shipments.create(self.order_id, operation_key="ship-1", quantity="59",
                                  source_ref="SYN-PART-1")
        with self.assertRaisesRegex(ValueError, "allocated"):
            self.operations.readiness(self.order_id, operation_key="less", status="PARTIAL",
                planned_at_utc="2026-11-01T00:00:00Z", actual_at_utc="2026-11-03T00:00:00Z",
                quantity="50", source_ref="SYN-MFG", reason="Correction", operator_confirmed=True)
        seq = self.shipments.plan_freight(self.first, operation_key="freight-1", **self.plan)
        self.assertEqual(self.shipments.plan_freight(self.first, operation_key="freight-1",
                                                     **self.plan), seq)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.shipments.plan_freight(self.first, operation_key="freight-1",
                **{**self.plan, "gross_weight_kg": "51"})


if __name__ == "__main__":
    unittest.main()
