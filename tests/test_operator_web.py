import io
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

from pilot_engine.config import AppConfig, Environment
from pilot_engine.operator_web import OperatorWeb, _case_page, _execute, _parse_form, make_handler
from pilot_engine.store import PilotStore
from pilot_engine.synthetic_demo import create_demo_handoff
from test_sell_drafts import SellDraftTests


class OperatorWebTests(unittest.TestCase):
    def setUp(self):
        SellDraftTests.setUp(self)
        self.app = OperatorWeb(AppConfig(Environment.TEST, self.store.scope, self.path.parent))

    def advance(self, expected, values=None, files=None):
        case = self.app.chain(self.handoff_id)
        case["store"] = self.store
        case["data_dir"] = self.path.parent
        self.assertEqual(self.app.step(case), expected)
        _execute(case, expected, {"confirm": "yes", **(values or {})}, files or {})

    def test_guided_visual_case_through_reviewed_documents(self):
        self.assertIn("Find devirleri".encode(), self.app_handoffs())
        self.advance("opportunity")
        self.advance("draft", {**self.sender, "language": "en"})
        page = _case_page(self.app, self.handoff_id).decode()
        self.assertIn("sales@example.org", page)
        self.assertIn("İleti içeriğini onayla", page)
        self.advance("send", {"reason": "Invented exact content reviewed",
                              "reviewed_claims": "yes"})
        self.advance("capture")
        self.advance("inbound", {"message": "Please quote 150 example clamps"})
        self.advance("review-inbound", {"classification": "RFQ_CANDIDATE",
                                        "explanation": "Invented buyer request"})
        self.advance("rfq", {"customer_reference": "SYN-WEB-RFQ",
                             "specification": "Connection clamp per example drawing",
                             "quantity": "150", "requested_terms": "FCA Example city"})
        self.advance("accept-rfq", {"reason": "Invented requirement review"})
        self.advance("price", {"unit_price_text": "12.50",
                               "valid_until_utc": "2030-01-01T00:00:00Z"},
                     {"price_document": b"invented price file"})
        self.advance("quote", {"valid_until_utc": "2029-12-01T00:00:00Z",
                               "exclusions": "Synthetic only"})
        self.advance("approve-quote", {"reason": "Invented quote review"})
        self.advance("po", {"buyer": "Example Buyer", "seller": "Example Clamp Works",
                            "sku": "EXAMPLE-001", "description": "Connection clamp per example drawing",
                            "quantity": "150", "unit": "PCS", "unit_price": "12.50",
                            "currency": "EUR", "total": "1875.00", "incoterm_code": "FCA",
                            "incoterm_place": "Example city",
                            "requested_delivery_at_utc": "2029-11-01T00:00:00Z"},
                     {"po_document": b"invented PO file"})
        self.advance("approve-po", {"reason": "Invented exact PO review"})
        self.advance("erp", {"customer_mapping_ref": "SYN-CUSTOMER-MAP",
                             "tax_mapping_ref": "SYN-TAX-MAP", "handed_to": "Example operator"})
        self.advance("readiness", {"planned_at_utc": "2029-10-01T00:00:00Z",
                                   "reason": "Invented manufacturer plan"})
        self.advance("freight", {"pickup": "Example factory", "delivery": "Example city",
                                 "packages": "1", "net_weight_kg": "100",
                                 "gross_weight_kg": "120", "dimensions": "100 x 40 x 30 cm"})
        self.advance("invoice", {"exporter_legal_id": "SYN-TR-123",
                                 "buyer_legal_id": "SYN-DE-456", "tax_review_ref": "SYN-TAX"})
        self.advance("review-invoice", {"reason": "Invented invoice review"})
        self.advance("packing", {"quantity": "150", "net_weight_kg": "100",
                                 "gross_weight_kg": "120", "dimensions": "100 x 40 x 30 cm",
                                 "marks": "SYN-MARK"})
        self.advance("review-packing", {"reason": "Invented packing review"})
        self.advance("checklist", {"name": "Origin evidence", "status": "NOT_REQUIRED",
                                   "owner": "Example operator"})
        self.advance("review-checklist", {"reason": "Invented checklist review"})
        case = self.app.chain(self.handoff_id)
        self.assertEqual(self.app.step(case), "complete")
        self.assertTrue(case["case"]["technical_case_accepted"])
        self.assertFalse(case["case"]["real_freight_booked"])

    def app_handoffs(self):
        from pilot_engine.operator_web import _dashboard
        return _dashboard(self.app)

    def test_local_http_session_and_csrf(self):
        with self.assertRaisesRegex(ValueError, "sentetik"):
            OperatorWeb(AppConfig(Environment.PILOT, self.store.scope, self.path.parent))
        handler_type = make_handler(self.app)

        def request(method, path, headers=None, body=b""):
            handler = handler_type.__new__(handler_type)
            handler.path = path
            handler.headers = headers or {}
            handler.rfile = io.BytesIO(body)
            handler.wfile = io.BytesIO()
            handler.server = type("Server", (), {"server_port": 8080})()
            handler.send_response = lambda code: setattr(handler, "status", code)
            handler.send_header = lambda key, value: None
            handler.end_headers = lambda: None
            getattr(handler, method)()
            return handler.status, handler.wfile.getvalue()

        self.assertEqual(request("do_GET", "/")[0], 403)
        self.assertEqual(request("do_GET", "/?token=" + self.app.session)[0], 303)
        cookie = "ego_session=" + self.app.session
        path = "/case?id=" + self.handoff_id
        status, page = request("do_GET", path, {"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn("Satış fırsatını aç".encode(), page)
        body = urlencode({"step": "opportunity", "confirm": "yes", "csrf": "invalid"})
        headers = {"Cookie": cookie, "Origin": "http://127.0.0.1:8080",
                   "Content-Type": "application/x-www-form-urlencoded",
                   "Content-Length": str(len(body))}
        self.assertEqual(request("do_POST", path, headers, body.encode())[0], 403)
        body = urlencode({"step": "opportunity", "confirm": "yes", "csrf": self.app.csrf})
        headers["Content-Length"] = str(len(body))
        self.assertEqual(request("do_POST", path, headers, body.encode())[0], 303)
        self.assertEqual(self.app.step(self.app.chain(self.handoff_id)), "draft")
        multipart = (b"--test\r\nContent-Disposition: form-data; name=\"price_document\"; "
                     b"filename=\"price.txt\"\r\nContent-Type: text/plain\r\n\r\ninvented\r\n"
                     b"--test--\r\n")
        fields, files = _parse_form("multipart/form-data; boundary=test", multipart)
        self.assertEqual((fields, files), ({}, {"price_document": b"invented"}))

    def test_demo_button_seeds_only_one_reviewable_find_handoff(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            store = PilotStore(path / "pilot.sqlite3")
            app = OperatorWeb(AppConfig(Environment.TEST, store.scope, path))
            handoff_id = create_demo_handoff(app.store)
            self.assertEqual(create_demo_handoff(app.store), handoff_id)
            self.assertEqual(app.step(app.chain(handoff_id)), "opportunity")
            self.assertEqual(len(app.handoffs()), 1)


if __name__ == "__main__":
    unittest.main()
