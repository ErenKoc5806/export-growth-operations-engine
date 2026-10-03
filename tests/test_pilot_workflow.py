import json
import unittest
from pathlib import Path

from pilot_engine.workflow import PilotValidationError, run_case


FIXTURE = Path(__file__).resolve().parents[1] / "pilot_engine" / "fixtures" / "732690_de_synthetic.json"


def case():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class PilotWorkflowTests(unittest.TestCase):
    def test_approved_case_links_find_sell_execute_and_reconciles_documents(self):
        result = run_case(case())
        self.assertEqual(result.sales_order["sku"], "SYN-CONNECTION-CLAMP-001")
        self.assertEqual([e["stage"] for e in result.audit_events], ["FIND", "SELL", "EXECUTE"])
        self.assertEqual(result.sales_order["customer_po_id"], "PO-SYN-001")
        self.assertEqual(result.sales_order["total"], result.commercial_invoice_draft["total"])
        self.assertEqual(result.sales_order["id"], result.packing_list_draft["order_id"])
        self.assertEqual(result.commercial_invoice_draft["status"], "DRAFT_REQUIRES_REVIEW")

    def test_missing_contact_evidence_and_unapproved_quote_block_progress(self):
        incomplete = case()
        incomplete["buyer"]["source_url"] = ""
        with self.assertRaisesRegex(PilotValidationError, "source_url"):
            run_case(incomplete)

        unapproved = case()
        unapproved["quotation"]["status"] = "DRAFT"
        with self.assertRaisesRegex(PilotValidationError, "human approver"):
            run_case(unapproved)

    def test_po_mismatch_and_invalid_totals_block_order_creation(self):
        mismatch = case()
        mismatch["customer_po"]["quantity"] = "101"
        with self.assertRaisesRegex(PilotValidationError, "differs"):
            run_case(mismatch)

        invalid_total = case()
        invalid_total["customer_po"]["total"] = "invalid"
        with self.assertRaisesRegex(PilotValidationError, "total is invalid"):
            run_case(invalid_total)

        bad_currency = case()
        bad_currency["quotation"]["currency"] = "USD"
        with self.assertRaisesRegex(PilotValidationError, "currency does not match"):
            run_case(bad_currency)


if __name__ == "__main__":
    unittest.main()
