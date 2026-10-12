import unittest
import json

from pilot_engine.config import AppConfig, Environment
from pilot_engine.operator_web import (OperatorWeb, _case_page, _date_field,
                                       _execute, _feedback_page, _save_feedback, _utc)
from test_sell_drafts import SellDraftTests


class OperatorPOWebTests(unittest.TestCase):
    def setUp(self):
        SellDraftTests.setUp(self)
        self.app = OperatorWeb(AppConfig(Environment.TEST, self.store.scope, self.path.parent))
        self.po = {"buyer": "Example Buyer", "seller": "Example Clamp Works",
                   "sku": "EXAMPLE-001", "description": "Connection clamp per example drawing",
                   "quantity": "150", "unit": "PCS", "unit_price": "12.50",
                   "currency": "EUR", "total": "1875.00", "incoterm_code": "FCA",
                   "incoterm_place": "Example city",
                   "requested_delivery_at_utc": "2029-11-01T00:00:00Z"}

    def advance(self, step, values=None, files=None):
        case = self.app.chain(self.handoff_id)
        case["data_dir"] = self.path.parent
        self.assertEqual(self.app.step(case), step)
        _execute(case, step, {"confirm": "yes", **(values or {})}, files or {})

    def to_po(self):
        self.advance("opportunity")
        self.advance("draft", {**self.sender, "language": "en"})
        self.advance("send", {"reason": "Invented content reviewed", "reviewed_claims": "yes"})
        self.advance("capture")
        self.advance("inbound", {"message": "Please quote example clamps"})
        self.advance("review-inbound", {"classification": "RFQ_CANDIDATE",
                                        "explanation": "Synthetic RFQ"})
        self.advance("rfq", {"customer_reference": "SYN-RFQ", "quantity": "150",
                             "specification": self.po["description"],
                             "requested_terms": "FCA Example city"})
        self.advance("accept-rfq", {"reason": "Invented requirement review"})
        self.advance("price", {"unit_price_text": "12.50",
                               "valid_until_utc": "2030-01-01T00:00:00Z"},
                     {"price_document": b"invented price document"})
        self.advance("quote", {"valid_until_utc": "2029-12-01T00:00:00Z",
                               "exclusions": "Synthetic only"})
        self.advance("approve-quote", {"reason": "Invented quote review"})
        self.assertEqual(self.app.step(self.app.chain(self.handoff_id)), "po")

    def test_wrong_po_entry_is_corrected_with_same_original_hash(self):
        self.to_po()
        original = b"invented source PO for this test"
        self.advance("po", {**self.po, "seller": "Incorrect seller"},
                     {"po_document": original})
        case = self.app.chain(self.handoff_id)
        self.assertEqual(self.app.step(case), "correct-po")
        page = _case_page(self.app, self.handoff_id).decode()
        self.assertIn("Teklif ve PO karşılaştırması", page)
        self.assertIn("Incorrect seller", page)
        self.assertIn("Aynı özgün sentetik PO dosyasını tekrar seçin", page)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            _execute(case, "correct-po", {"confirm": "yes", **self.po},
                     {"po_document": b"wrong original"})
        self.assertEqual(self.app.chain(self.handoff_id)["po"]["revision"], 1)
        _execute(case, "correct-po", {"confirm": "yes", **self.po},
                 {"po_document": original})
        revised = self.app.chain(self.handoff_id)
        self.assertEqual((revised["po"]["revision"], revised["po"]["differences"]), (2, []))
        self.advance("approve-po", {"reason": "Verified corrected entry against original"})
        self.assertEqual(self.app.chain(self.handoff_id)["case"]["operations"]
                         ["order"]["payload"]["commercial_version"], 1)

    def test_reviewed_buyer_quantity_amendment_in_visual_flow(self):
        self.to_po()
        amended = {**self.po, "buyer": "Example Buyer GmbH", "quantity": "100",
                   "total": "1250.00"}
        self.advance("po", amended, {"po_document": b"invented partial PO"})
        case = self.app.chain(self.handoff_id)
        self.assertEqual(self.app.step(case), "approve-po")
        page = _case_page(self.app, self.handoff_id).decode()
        self.assertIn("Example Buyer GmbH", page)
        self.assertIn("PO girişindeki alanları düzelt", page)
        with self.assertRaisesRegex(ValueError, "amendment"):
            _execute(case, "approve-po", {"confirm": "yes", "reason": "Incomplete review",
                "review_buyer": "yes", "review_quantity": "yes"}, {})
        self.advance("approve-po", {"reason": "Original PO and every changed field reviewed",
            "review_buyer": "yes", "review_quantity": "yes", "review_total": "yes"})
        order = self.app.chain(self.handoff_id)["case"]["operations"]["order"]
        self.assertEqual((order["payload"]["quantity"],
                          order["payload"]["commercial_version"]), ("100", 2))
        self.assertEqual(order["payload"]["reviewed_differences"],
                         ["buyer", "quantity", "total"])

    def test_increased_quantity_requires_correction_instead_of_approval(self):
        self.to_po()
        self.advance("po", {**self.po, "quantity": "151", "total": "1887.50"},
                     {"po_document": b"invented overquoted PO"})
        self.assertEqual(self.app.step(self.app.chain(self.handoff_id)), "correct-po")

    def test_istanbul_date_widget_round_trips_to_utc(self):
        control = _date_field("when", "Zaman", "2029-11-01T00:00:00Z")
        self.assertIn('type="datetime-local"', control)
        self.assertIn('value="2029-11-01T03:00"', control)
        self.assertEqual(_utc("2029-11-01T03:00"), "2029-11-01T00:00:00+00:00")

    def test_feedback_form_records_self_reported_time_and_corrections(self):
        self.to_po()
        self.advance("po", self.po, {"po_document": b"invented PO"})
        self.advance("approve-po", {"reason": "Reviewed source"})
        page = _feedback_page(self.app, self.handoff_id).decode()
        self.assertIn("Geçen süre (dakika)", page)
        case = self.app.chain(self.handoff_id)
        values = {"confirm": "yes", "operator_role": "Test operator", "minutes": "18",
                  "corrections": "2", "hesitation_step": "PO karşılaştırması",
                  "friction": "Date entry", "suggestion": "Show local clock"}
        with self.assertRaisesRegex(ValueError, "tam sayı"):
            _save_feedback(self.app, case, {**values, "minutes": "1.5"})
        _save_feedback(self.app, case, values)
        record = json.loads((self.path.parent / "operator-feedback.jsonl").read_text())
        self.assertEqual((record["minutes"], record["corrections"],
                          record["operator_role"]), (18, 2, "Test operator"))
        self.assertEqual(record["data_origin"], "SYNTHETIC")


if __name__ == "__main__":
    unittest.main()
