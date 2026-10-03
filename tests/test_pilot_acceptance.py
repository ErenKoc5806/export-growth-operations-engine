import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pilot_engine.acceptance import (MockERP, MockMail, SyntheticSendApproval,
                                     run_synthetic_acceptance)
from pilot_engine.store import PilotStore
from pilot_engine.workflow import PilotValidationError


FIXTURE = Path(__file__).resolve().parents[1] / "pilot_engine" / "fixtures" / "732690_de_synthetic.json"


class PilotAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.case = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.store = PilotStore(Path(self.temp.name) / "acceptance.sqlite3")
        self.mail = MockMail()
        self.erp = MockERP()
        self.content = "Synthetic clamp inquiry"
        self.approval = SyntheticSendApproval(
            self.case["buyer"]["business_email"],
            hashlib.sha256(self.content.encode()).hexdigest(), "fixture-reviewer")

    def run_case(self):
        return run_synthetic_acceptance(self.case, self.store, self.mail, self.erp,
                                        self.approval, self.content)

    def test_linked_mock_find_sell_execute_replay(self):
        result = self.run_case()
        self.run_case()
        self.assertEqual(len(self.mail.messages), 1)
        self.assertEqual(len(self.erp.exports), 1)
        self.assertEqual(self.store.read_summary(result.opportunity_id)["order_id"],
                         result.sales_order["id"])
        self.assertEqual(result.commercial_invoice_draft["order_id"], result.sales_order["id"])
        self.assertEqual(result.packing_list_draft["order_id"], result.sales_order["id"])

    def test_rejection_and_invalid_po_stop_before_mock_side_effects(self):
        wrong = SyntheticSendApproval(self.approval.recipient, "wrong-hash", "fixture-reviewer")
        with self.assertRaises(ValueError):
            run_synthetic_acceptance(self.case, self.store, self.mail, self.erp,
                                     wrong, self.content)
        invalid = copy.deepcopy(self.case)
        invalid["customer_po"]["total"] = "9999.00"
        with self.assertRaises(PilotValidationError):
            run_synthetic_acceptance(invalid, self.store, self.mail, self.erp,
                                     self.approval, self.content)
        self.assertEqual((len(self.mail.messages), len(self.erp.exports)), (0, 0))

    def test_unknown_mock_delivery_does_not_send_again(self):
        self.mail.uncertain_keys.add(f"outreach:{self.case['opportunity_id']}")
        with self.assertRaisesRegex(RuntimeError, "reconciliation"):
            self.run_case()
        self.assertEqual((len(self.mail.messages), len(self.erp.exports)), (0, 0))
        self.assertIsNone(self.store.read_summary(self.case["opportunity_id"]))


if __name__ == "__main__":
    unittest.main()
