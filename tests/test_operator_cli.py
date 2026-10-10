import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from pilot_engine.config import AppConfig, Environment
from pilot_engine.operator_cli import main
from test_sell_drafts import SellDraftTests


class OperatorCLITests(unittest.TestCase):
    def setUp(self):
        SellDraftTests.setUp(self)
        self.config = AppConfig(Environment.TEST, self.store.scope, self.path.parent)
        self.form_file = self.path.parent / "operator-input.json"

    def act(self, name, **payload):
        self.form_file.write_text(json.dumps(payload), encoding="utf-8")
        output = io.StringIO()
        with patch("builtins.input", return_value=f"CONFIRM {name}"), redirect_stdout(output):
            self.assertEqual(main(["act", name, str(self.form_file)], config=self.config), 0)
        return json.loads(output.getvalue().split('{\n  "result": ')[-1].rsplit("\n}", 1)[0])

    def show(self, kind, record_id):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["show", kind, record_id], config=self.config), 0)
        return json.loads(output.getvalue().split("\n", 1)[1])

    def test_confirmed_synthetic_case_from_find_handoff_to_documents(self):
        handoff = self.show("handoff", self.handoff_id)
        self.assertEqual(handoff["status"], "CURRENT_RESEARCH")
        self.assertEqual(handoff["data_origin"], "SYNTHETIC")
        opportunity = self.act("opportunity-open", handoff_id=self.handoff_id)
        draft_id, revision = self.act("draft-create", handoff_id=self.handoff_id,
                                      **self.sender, language="en", claim_refs=[])
        self.assertEqual(self.show("draft", draft_id)["status"], "CURRENT_DRAFT")
        digest = self.delivery_preview(draft_id, revision)
        self.act("send-approve", draft_id=draft_id, revision=revision,
                 decision="APPROVE", reason="Reviewed invented envelope and claims",
                 expected_envelope_sha256=digest, reviewed_claims=True)
        attempt = self.act("send-capture", draft_id=draft_id, revision=revision)
        self.assertEqual(attempt["outcome"], "CAPTURED_NOT_DELIVERED")
        inbound_id = self.act("inbound-record", source_ref="SYN-CLI-RFQ",
                              raw_ref="SYN-CLI-RAW", received_at_utc="2026-10-06T02:00:00Z",
                              channel="EMAIL", provider_message_id=attempt["provider_message_id"])
        self.act("inbound-review", inbound_id=inbound_id, opportunity_id=opportunity,
                 classification="RFQ_CANDIDATE", evidence_ref="SYN-CLI-RAW",
                 explanation="Invented request")
        rfq_id, _ = self.act("rfq-save", inbound_id=inbound_id, payload={
            "sku": "EXAMPLE-001", "specification": "Example drawing", "quantity": "150",
            "unit": "PCS", "destination": "DE", "requested_terms": {}})
        self.act("rfq-decide", rfq_id=rfq_id, revision=1, decision="ACCEPT",
                 reason="Reviewed invented requirements")
        price_file = self.path.parent / "synthetic-price.txt"
        price_file.write_bytes(b"invented test price document")
        authority = self.act("price-register", product_id=self.product_id,
                             source_ref="SYN-CLI-PRICE", content_file=str(price_file),
                             sku="EXAMPLE-001", currency="EUR", unit_price_text="12.50",
                             valid_until_utc="2030-01-01T00:00:00Z")
        quote_id, _ = self.act("quote-save", rfq_id=rfq_id, price_authority_id=authority,
                               valid_until_utc="2029-12-01T00:00:00Z", exclusions="Synthetic")
        quote = self.show("quote", quote_id)
        self.act("quote-decide", quotation_id=quote_id, revision=1, decision="APPROVE",
                 expected_payload_sha256=quote["payload_sha256"], reason="Invented quote review")
        po_file = self.path.parent / "synthetic-po.txt"
        po_file.write_bytes(b"invented PO bytes")
        po_id, _ = self.act("po-save", quotation_id=quote_id,
                            source_ref="SYN-CLI-PO", original_ref="SYN-CLI-PO-DOC",
                            original_file=str(po_file), received_at_utc="2026-10-06T12:00:00Z",
                            payload={"buyer": "Example Buyer", "seller": "Example Clamp Works",
                                     "sku": "EXAMPLE-001", "description": "Example drawing",
                                     "quantity": "150", "unit": "PCS", "unit_price": "12.50",
                                     "currency": "EUR", "total": "1875.00", "incoterm_code": "FCA",
                                     "incoterm_place": "Example city",
                                     "requested_delivery_at_utc": "2029-11-01T00:00:00Z"})
        po = self.show("po", po_id)
        self.assertEqual(po["differences"], [])
        order_id = self.act("po-decide", po_id=po_id, revision=1, decision="APPROVE",
                            expected_payload_sha256=po["payload_sha256"],
                            reason="Exact invented PO reviewed")
        self.act("erp-handoff", order_id=order_id, operation_key="SYN-CLI-ERP",
                 customer_mapping_ref="SYN-CUSTOMER", tax_mapping_ref="SYN-TAX",
                 handed_to="Example operator", evidence_ref="SYN-ERP-REVIEW")
        self.act("readiness-record", order_id=order_id, operation_key="SYN-CLI-READY",
                 status="PENDING", planned_at_utc="2026-11-01T00:00:00Z",
                 actual_at_utc=None, quantity=None, source_ref="SYN-PLAN",
                 reason="Awaiting invented manufacturer")
        self.act("freight-plan", order_id=order_id, operation_key="SYN-CLI-FREIGHT",
                 status="REQUESTED", pickup="Example factory", delivery="Example city",
                 packages=1, net_weight_kg="100", gross_weight_kg="120",
                 dimensions="100 x 40 x 30 cm", requested_at_utc="2026-11-03T00:00:00Z")
        packing_id, _ = self.act("packing-draft", order_id=order_id, marks="SYN-MARK",
                                 packages=[{"quantity": "150", "net_weight_kg": "100",
                                            "gross_weight_kg": "120", "dimensions": "100 x 40 x 30 cm"}])
        packing = self.show("document", packing_id)
        self.act("document-review", document_id=packing_id, revision=1,
                 decision="REVIEW", expected_payload_sha256=packing["payload_sha256"],
                 reason="Invented packing reviewed")
        self.act("export-facts-review", order_id=order_id, hs_code="732690",
                 classification_evidence_ref="SYN-HS", origin_country_code="TR",
                 origin_evidence_ref="SYN-ORIGIN", manufacturer_review_ref="SYN-MFG")
        invoice_id, _ = self.act("invoice-draft", order_id=order_id,
                                 exporter_legal_id="SYN-TR-123", buyer_legal_id="SYN-DE-456",
                                 tax_review_ref="SYN-TAX-REVIEW",
                                 destination_review_ref="SYN-DE-REVIEW")
        checklist_id, _ = self.act("checklist-draft", order_id=order_id,
                                   items=[{"name": "Origin evidence", "status": "NOT_REQUIRED",
                                           "owner": "Operator", "evidence_ref": "SYN-REVIEW"}])
        for document_id in (invoice_id, checklist_id):
            record = self.show("document", document_id)
            self.act("document-review", document_id=document_id, revision=1,
                     decision="REVIEW", expected_payload_sha256=record["payload_sha256"],
                     reason="Invented evidence reviewed")
        case = self.show("case", order_id)
        self.assertTrue(case["technical_case_accepted"])
        self.assertFalse(case["real_freight_booked"])
        self.assertFalse(case["manufacturer_documents_released"])
        self.form_file.write_text(json.dumps({
            "order_id": order_id, "friction": "Invoice references needed a second look",
            "suggestion": "Show a reconciliation checklist"}), encoding="utf-8")
        with patch("builtins.input", return_value="CONFIRM feedback"), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["feedback", str(self.form_file)], config=self.config), 0)
        feedback = json.loads((self.path.parent / "operator-feedback.jsonl").read_text())
        self.assertEqual(feedback["order_id"], order_id)
        self.assertEqual(feedback["data_origin"], "SYNTHETIC")

    def delivery_preview(self, draft_id, revision):
        output = io.StringIO()
        with redirect_stdout(output):
            main(["send-preview", draft_id, str(revision)], config=self.config)
        return json.loads(output.getvalue())["envelope_sha256"]

    def test_cancel_and_pilot_environment_fail_closed(self):
        self.form_file.write_text(json.dumps({"handoff_id": self.handoff_id}), encoding="utf-8")
        with patch("builtins.input", return_value="no"), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "cancelled"):
                main(["act", "opportunity-open", str(self.form_file)], config=self.config)
        with self.assertRaisesRegex(ValueError, "synthetic only"):
            main(["queue", "handoff"], config=AppConfig(
                Environment.PILOT, self.store.scope, self.path.parent))


if __name__ == "__main__":
    unittest.main()
