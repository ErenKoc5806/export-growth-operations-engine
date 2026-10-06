import sqlite3
import unittest
from contextlib import closing

from pilot_engine.execute_operations import ExecuteOperations
import test_execute_order


class ExecuteOperationsTests(unittest.TestCase):
    def setUp(self):
        test_execute_order.ExecuteOrderTests.setUp(self)
        po_id, _ = self.execute.save(self.quote_id, payload=self.po, **self.inputs)
        self.order_id = self.execute.decide(
            po_id, 1, "APPROVE", expected_payload_sha256=self.execute.read(po_id)["payload_sha256"],
            reason="Synthetic PO matches approved quote")
        self.operations = ExecuteOperations(self.store)

    def test_manual_handoff_and_readiness_do_not_claim_external_actions(self):
        args = dict(operation_key="handoff-1", customer_mapping_ref="SYN-CUSTOMER-MAP",
                    tax_mapping_ref="SYN-TAX-MAP", handed_to="Example operator",
                    evidence_ref="SYN-MANUAL-EXPORT")
        event = self.operations.manual_erp_handoff(self.order_id, **args)
        self.assertEqual(self.operations.manual_erp_handoff(self.order_id, **args), event)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.operations.manual_erp_handoff(self.order_id,
                                               **{**args, "handed_to": "Different"})
        base = dict(planned_at_utc="2026-11-01T00:00:00Z", source_ref="SYN-MFG-1",
                    reason="Invented manufacturer update")
        with self.assertRaisesRegex(ValueError, "confirmation"):
            self.operations.readiness(self.order_id, operation_key="bad", status="READY",
                                      actual_at_utc="2026-11-01T00:00:00Z", quantity="150",
                                      **base)
        self.operations.readiness(self.order_id, operation_key="pending", status="PENDING",
                                  actual_at_utc=None, quantity=None, **base)
        self.operations.readiness(self.order_id, operation_key="ready", status="READY",
                                  actual_at_utc="2026-11-02T00:00:00Z", quantity="150",
                                  operator_confirmed=True, **base)
        summary = self.operations.summary(self.order_id)
        self.assertEqual(summary["readiness"]["payload"]["status"], "READY")
        self.assertFalse(summary["erp_created"])

    def test_freight_needs_packing_and_booking_needs_evidence(self):
        plan = dict(pickup="Example factory", delivery="Example city",
                    requested_at_utc="2026-11-03T00:00:00Z")
        self.operations.plan_freight(self.order_id, operation_key="estimate",
                                     status="ESTIMATED", packages=None,
                                     net_weight_kg=None, gross_weight_kg=None, **plan)
        with self.assertRaisesRegex(ValueError, "packing"):
            self.operations.plan_freight(self.order_id, operation_key="invalid",
                                         status="REQUESTED", packages=None,
                                         net_weight_kg="100", gross_weight_kg="120", **plan)
        self.operations.plan_freight(self.order_id, operation_key="request",
                                     status="REQUESTED", packages=10,
                                     net_weight_kg="100", gross_weight_kg="120", **plan)
        with self.assertRaisesRegex(ValueError, "readiness"):
            self.operations.confirm_booking(self.order_id, operation_key="premature",
                                            confirmation_ref="SYN-CARRIER-1",
                                            source_ref="SYN-RECEIPT-1",
                                            confirmed_at_utc="2026-11-04T00:00:00Z")
        self.operations.readiness(self.order_id, operation_key="ready", status="READY",
                                  planned_at_utc="2026-11-01T00:00:00Z",
                                  actual_at_utc="2026-11-02T00:00:00Z", quantity="150",
                                  source_ref="SYN-MFG-1", reason="Invented evidence",
                                  operator_confirmed=True)
        self.operations.confirm_booking(self.order_id, operation_key="booking",
                                        confirmation_ref="SYN-CARRIER-1",
                                        source_ref="SYN-RECEIPT-1",
                                        confirmed_at_utc="2026-11-04T00:00:00Z")
        self.assertFalse(self.operations.summary(self.order_id)["booking_confirmation"]
                         ["payload"]["real_carrier_booking"])
        with closing(sqlite3.connect(self.path)) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("DELETE FROM execute_operation_event")


if __name__ == "__main__":
    unittest.main()
