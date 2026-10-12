import unittest
import io
from urllib.parse import urlencode

from pilot_engine.config import AppConfig, Environment
from pilot_engine.operator_shipments import _context, _next, shipment_execute, shipment_page
from pilot_engine.operator_web import OperatorWeb, make_handler
import test_execute_operations


class OperatorShipmentTests(unittest.TestCase):
    def setUp(self):
        test_execute_operations.ExecuteOperationsTests.setUp(self)
        self.operations.manual_erp_handoff(self.order_id, operation_key="SYN-ERP-UI",
            customer_mapping_ref="SYN-MAP", tax_mapping_ref="SYN-TAX",
            handed_to="Example operator", evidence_ref="SYN-ERP-EVIDENCE")
        self.app = OperatorWeb(AppConfig(Environment.TEST, self.store.scope, self.path.parent))
        self.handoff_id = self.app.handoffs()[0]["id"]
        self.at = "2026-11-02T00:00:00Z"

    def act(self, action, shipment_id=None, **values):
        return shipment_execute(self.app, self.handoff_id, shipment_id,
                                {"action": action, "confirm": "yes", **values})

    def readiness(self, quantity, status):
        self.act("readiness", status=status, quantity=quantity,
                 planned_at_utc="2026-11-01T00:00:00Z", actual_at_utc=self.at,
                 reason="Invented manufacturer evidence")

    def test_buttons_create_two_shipments_and_review_independent_documents(self):
        page = shipment_page(self.app, self.handoff_id).decode()
        self.assertIn("Hazır toplam adet", page)
        self.readiness("60", "PARTIAL")
        self.assertIn("Hazır toplam adet", shipment_page(self.app, self.handoff_id).decode())
        first = self.act("create", quantity="60")
        self.assertIn("Navlun taslağı", shipment_page(self.app, self.handoff_id, first).decode())
        with self.assertRaisesRegex(ValueError, "değişti"):
            self.act("create", first, quantity="1")
        self.act("freight", first, pickup="Example factory", delivery="Example city",
                 net_weight_kg="40", gross_weight_kg="50", dimensions="40 x 30 x 20 cm")
        self.act("packing", first, quantity="60", net_weight_kg="40",
                 gross_weight_kg="50", dimensions="40 x 30 x 20 cm", marks="SYN-MARK")
        self.act("review-packing", first, reason="Synthetic packing evidence")
        self.act("export-facts", first, hs_code="732690",
            classification_evidence_ref="SYN-HS", origin_country_code="TR",
            origin_evidence_ref="SYN-ORIGIN", manufacturer_review_ref="SYN-MFG")
        self.act("invoice", first, exporter_legal_id="SYN-TR", buyer_legal_id="SYN-DE",
                 tax_review_ref="SYN-TAX", destination_review_ref="SYN-DE-REVIEW")
        self.act("review-invoice", first, reason="Synthetic invoice review")
        self.act("booking", first, outcome="UNKNOWN", confirmation_ref="SYN-CARRIER",
                 source_ref="SYN-RECEIPT")
        self.assertEqual(_next(_context(self.app, self.handoff_id, first)), "complete")
        self.assertIn("Gerçek sevk edilen: 0", shipment_page(self.app, self.handoff_id, first).decode())
        self.readiness("150", "READY")
        second = self.act("create", quantity="90")
        self.assertNotEqual(first, second)
        self.assertEqual(_context(self.app, self.handoff_id)["summary"]["unallocated_quantity"], "0")
        with self.assertRaisesRegex(ValueError, "exceeds"):
            self.act("create", quantity="1")
        self.assertEqual(_next(_context(self.app, self.handoff_id, second)), "freight")

    def test_unknown_shipment_and_legacy_path_are_blocked(self):
        with self.assertRaisesRegex(ValueError, "ait değil"):
            shipment_page(self.app, self.handoff_id, "ES-not-from-order")
        self.operations.plan_freight(self.order_id, operation_key="legacy", status="ESTIMATED",
            pickup="Example factory", delivery="Example city", packages=None,
            net_weight_kg=None, gross_weight_kg=None,
            requested_at_utc="2026-11-03T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "parçalara bölünemez"):
            shipment_page(self.app, self.handoff_id)

    def test_shipment_route_requires_session_origin_and_csrf(self):
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

        path = "/shipments?id=" + self.handoff_id
        self.assertEqual(request("do_GET", path)[0], 403)
        cookie = "ego_session=" + self.app.session
        status, page = request("do_GET", path, {"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn("Kısmi sevkiyatlar".encode(), page)
        body = urlencode({"action": "create", "quantity": "1", "confirm": "yes",
                          "csrf": "wrong"}).encode()
        headers = {"Cookie": cookie, "Origin": "http://127.0.0.1:8080",
                   "Content-Type": "application/x-www-form-urlencoded",
                   "Content-Length": str(len(body))}
        self.assertEqual(request("do_POST", path, headers, body)[0], 403)
        headers["Origin"] = "http://example.com"
        self.assertEqual(request("do_POST", path, headers, body)[0], 403)


if __name__ == "__main__":
    unittest.main()
