import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from pilot_engine.execute_documents import ExecuteDocuments
import test_execute_operations


class ExecuteDocumentsTests(unittest.TestCase):
    def setUp(self):
        test_execute_operations.ExecuteOperationsTests.setUp(self)
        self.documents = ExecuteDocuments(self.store)
        self.plan = dict(pickup="Example factory", delivery="Example city",
                         requested_at_utc="2026-11-03T00:00:00Z")
        self.operations.plan_freight(self.order_id, operation_key="freight",
                                     status="REQUESTED", packages=1,
                                     net_weight_kg="100", gross_weight_kg="120",
                                     dimensions="100 x 40 x 30 cm", **self.plan)
        self.package = [{"quantity": "150", "net_weight_kg": "100",
                         "gross_weight_kg": "120", "dimensions": "100 x 40 x 30 cm"}]

    def test_draft_review_and_end_to_end_case(self):
        self.operations.manual_erp_handoff(
            self.order_id, operation_key="SYN-ERP-1",
            customer_mapping_ref="SYN-CUSTOMER-MAP", tax_mapping_ref="SYN-TAX-MAP",
            handed_to="Example operator", evidence_ref="SYN-EXPORT-REVIEW")
        self.operations.readiness(
            self.order_id, operation_key="SYN-READINESS-1", status="PENDING",
            planned_at_utc="2026-11-01T00:00:00Z", actual_at_utc=None,
            quantity=None, source_ref="SYN-MFG-PLAN", reason="Awaiting manufacturer")
        invoice_id, _ = self.documents.invoice(self.order_id, exporter_legal_id=None,
                                               buyer_legal_id=None, tax_review_ref=None)
        view = self.documents.read(invoice_id)
        self.assertEqual(view["status"], "REVIEW_REQUIRED")
        with self.assertRaisesRegex(ValueError, "Missing legal"):
            self.documents.review(invoice_id, 1, decision="REVIEW",
                                  expected_payload_sha256=view["payload_sha256"],
                                  reason="Missing legal inputs")
        self.assertFalse(self.documents.case_summary(self.order_id)["technical_case_accepted"])
        self.documents.invoice(self.order_id, document_id=invoice_id, expected_revision=1,
                               exporter_legal_id="SYN-TR-123", buyer_legal_id="SYN-DE-456",
                               tax_review_ref="SYN-TAX-REVIEW")
        invoice = self.documents.read(invoice_id)
        self.documents.review(invoice_id, 2, decision="REVIEW",
                              expected_payload_sha256=invoice["payload_sha256"],
                              reason="Reviewed invented legal and tax fields")
        packing_id, _ = self.documents.packing(self.order_id, packages=self.package,
                                               marks="SYN-MARK-1")
        packing = self.documents.read(packing_id)
        self.documents.review(packing_id, 1, decision="REVIEW",
                              expected_payload_sha256=packing["payload_sha256"],
                              reason="Reviewed packing totals")
        checklist_id, _ = self.documents.checklist(self.order_id, items=[
            {"name": "Origin evidence", "status": "NOT_REQUIRED", "owner": "Operator",
             "evidence_ref": "SYN-CORRIDOR-REVIEW"}])
        checklist = self.documents.read(checklist_id)
        self.documents.review(checklist_id, 1, decision="REVIEW",
                              expected_payload_sha256=checklist["payload_sha256"],
                              reason="Reviewed invented document list")
        case = self.documents.case_summary(self.order_id)
        self.assertTrue(case["technical_case_accepted"])
        self.assertEqual(case["issues"], [])
        metric = self.documents.record_case_metric(
            self.order_id, event_key="SYN-TIME-1", minutes=12, corrections=1,
            failures=0, evidence_ref="SYN-OPERATOR-NOTE")
        self.assertEqual(self.documents.record_case_metric(
            self.order_id, event_key="SYN-TIME-1", minutes=12, corrections=1,
            failures=0, evidence_ref="SYN-OPERATOR-NOTE"), metric)
        case = self.documents.case_summary(self.order_id)
        self.assertEqual((case["operator_time_minutes"], case["corrections"]), (12, 1))
        self.assertEqual(case["find_to_sell"]["candidate_name"], "Example Buyer")
        self.assertFalse(case["manufacturer_documents_released"])
        self.assertFalse(case["physical_shipment_completed"])

        class AfterExpiry(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2031, 1, 1, tzinfo=timezone.utc)

        with patch("pilot_engine.quotations.datetime", AfterExpiry):
            self.assertEqual(self.quotes.read(self.quote_id)["status"], "EXPIRED")
            self.assertTrue(self.documents.case_summary(self.order_id)["technical_case_accepted"])
            self.assertEqual(self.documents.read(invoice_id)["status"], "REVIEWED_SYNTHETIC")

    def test_packing_mismatch_and_changed_freight_stales_review(self):
        with self.assertRaisesRegex(ValueError, "reconcile"):
            self.documents.packing(self.order_id,
                                   packages=[{**self.package[0], "quantity": "149"}],
                                   marks="SYN-MARK-1")
        packing_id, _ = self.documents.packing(self.order_id, packages=self.package,
                                               marks="SYN-MARK-1")
        view = self.documents.read(packing_id)
        self.documents.review(packing_id, 1, decision="REVIEW",
                              expected_payload_sha256=view["payload_sha256"],
                              reason="Invented package review")
        self.operations.plan_freight(self.order_id, operation_key="freight-revised",
                                     status="REQUESTED", packages=1,
                                     net_weight_kg="100", gross_weight_kg="120",
                                     dimensions="100 x 40 x 30 cm", **self.plan)
        self.assertEqual(self.documents.read(packing_id)["status"], "REVIEW_REQUIRED")
        self.assertFalse(self.documents.case_summary(self.order_id)["technical_case_accepted"])


if __name__ == "__main__":
    unittest.main()
