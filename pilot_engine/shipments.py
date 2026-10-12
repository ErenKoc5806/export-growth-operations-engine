"""Versioned synthetic partial shipments; no physical shipment or carrier call."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from decimal import Decimal
from uuid import uuid4

from pilot_engine.execute_operations import ExecuteOperations
from pilot_engine.execute_order import _amount
from pilot_engine.manual_contact import _text, _time
from pilot_engine.store import PilotStore, _json, _utc_now


def _digest(payload: dict) -> str:
    return hashlib.sha256(_json(payload).encode()).hexdigest()


class ExecuteShipments:
    def __init__(self, store: PilotStore):
        self.store = store
        self.operations = ExecuteOperations(store)

    def _shipment(self, db: object, shipment_id: str) -> tuple[dict, dict]:
        row = db.execute("SELECT * FROM execute_shipment WHERE id = ?", (shipment_id,)).fetchone()
        if row is None:
            raise ValueError("Shipment does not exist")
        order = self.operations.orders._order(db, row["order_id"])
        if (order is None or order["status"] != "LOCAL_ORDER_SYNTHETIC"
                or order["payload_sha256"] != row["order_sha256"]):
            raise ValueError("Shipment's approved order is no longer current")
        return dict(row), order

    def _ready(self, db: object, order_id: str, minimum: Decimal) -> None:
        latest = self.operations._latest(db, order_id, "READINESS")
        if (latest is None or latest["payload"]["status"] not in ("PARTIAL", "READY")
                or Decimal(latest["payload"]["quantity"]) < minimum):
            raise ValueError("Confirmed cumulative goods readiness is insufficient")

    def create(self, order_id: str, *, operation_key: str, quantity: str,
               source_ref: str) -> str:
        actor = self.store._require_access("EDIT_EXECUTE", order_id)
        key = _text(operation_key, "operation key", 120)
        source = _text(source_ref, "shipment source", 300)
        amount = _amount(quantity)
        if not source.startswith("SYN-") or amount is None or amount != amount.to_integral_value():
            raise ValueError("Synthetic positive whole-unit shipment and source required")
        with self.store._transaction() as db:
            prior = db.execute("SELECT * FROM execute_shipment WHERE operation_key = ?", (key,)).fetchone()
            if prior:
                if (prior["order_id"], Decimal(prior["quantity"]), prior["source_ref"]) != (
                        order_id, amount, source):
                    raise ValueError("Shipment operation key conflicts")
                return prior["id"]
            order = self.operations.orders._order(db, order_id)
            if order is None or order["status"] != "LOCAL_ORDER_SYNTHETIC":
                raise ValueError("Current approved local order required")
            if (db.execute("SELECT 1 FROM execute_document WHERE order_id = ?", (order_id,)).fetchone()
                    or db.execute("""SELECT 1 FROM execute_operation_event WHERE order_id = ?
                        AND kind IN ('FREIGHT_PLAN','BOOKING_CONFIRMATION')""", (order_id,)).fetchone()):
                raise ValueError("Existing order-wide documents or freight cannot mix with shipments")
            allocated = sum((Decimal(row[0]) for row in db.execute(
                "SELECT quantity FROM execute_shipment WHERE order_id = ?", (order_id,))), Decimal(0))
            cumulative = allocated + amount
            if cumulative > Decimal(order["payload"]["quantity"]):
                raise ValueError("Cumulative shipment exceeds approved order")
            self._ready(db, order_id, cumulative)
            shipment_id = f"ES-{uuid4()}"
            db.execute("""INSERT INTO execute_shipment
                (id, operation_key, order_id, order_sha256, quantity, source_ref, actor_id, created_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (shipment_id, key, order_id,
                order["payload_sha256"], str(amount), source, actor, _utc_now()))
            return shipment_id

    @staticmethod
    def _freight(db: object, shipment_id: str) -> dict | None:
        row = db.execute("""SELECT * FROM execute_shipment_freight WHERE shipment_id = ?
            ORDER BY sequence DESC LIMIT 1""", (shipment_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def plan_freight(self, shipment_id: str, *, operation_key: str, pickup: str,
                     delivery: str, packages: int, net_weight_kg: str,
                     gross_weight_kg: str, dimensions: str,
                     requested_at_utc: str) -> int:
        actor = self.store._require_access("EDIT_EXECUTE", shipment_id)
        key = _text(operation_key, "operation key", 120)
        if type(packages) is not int or packages <= 0:
            raise ValueError("Packages must be positive")
        net, gross = _amount(net_weight_kg), _amount(gross_weight_kg)
        if net is None or gross is None or gross < net:
            raise ValueError("Freight weights are inconsistent")
        payload = {"status": "REQUESTED", "pickup": _text(pickup, "pickup", 500),
                   "delivery": _text(delivery, "delivery", 500), "packages": packages,
                   "net_weight_kg": str(net), "gross_weight_kg": str(gross),
                   "dimensions": _text(dimensions, "dimensions", 500),
                   "requested_at_utc": _time(requested_at_utc),
                   "carrier_booked": False, "data_origin": "SYNTHETIC"}
        with self.store._transaction() as db:
            prior = db.execute("SELECT * FROM execute_shipment_freight WHERE operation_key = ?",
                               (key,)).fetchone()
            if prior:
                if prior["shipment_id"] != shipment_id or prior["payload_sha256"] != _digest(payload):
                    raise ValueError("Freight operation key conflicts")
                return prior["sequence"]
            shipment, order = self._shipment(db, shipment_id)
            if payload["delivery"] != order["payload"]["incoterm_place"]:
                raise ValueError("Delivery place differs from approved order")
            self._ready(db, shipment["order_id"], Decimal(shipment["quantity"]))
            cur = db.execute("""INSERT INTO execute_shipment_freight
                (operation_key, shipment_id, payload_json, payload_sha256, actor_id, recorded_at_utc)
                VALUES (?, ?, ?, ?, ?, ?)""", (key, shipment_id, _json(payload),
                _digest(payload), actor, _utc_now()))
            return cur.lastrowid

    def _document(self, db: object, document_id: str) -> dict | None:
        row = db.execute("""SELECT d.shipment_id, d.kind, r.* FROM execute_shipment_document d
            JOIN execute_shipment_document_revision r ON r.document_id = d.id
            WHERE d.id = ? ORDER BY r.revision DESC LIMIT 1""", (document_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        payload = json.loads(result.pop("payload_json"))
        result["payload"] = payload
        try:
            shipment, order = self._shipment(db, row["shipment_id"])
            current = row["order_sha256"] == order["payload_sha256"]
            self._ready(db, shipment["order_id"], Decimal(shipment["quantity"]))
        except ValueError:
            current = False
            shipment = None
        freight = self._freight(db, row["shipment_id"])
        if row["kind"] == "PACKING":
            current = current and freight is not None and freight["sequence"] == row["freight_sequence"]
        else:
            packing_id = db.execute("""SELECT id FROM execute_shipment_document
                WHERE shipment_id = ? AND kind = 'PACKING'""", (row["shipment_id"],)).fetchone()
            packing = self._document(db, packing_id["id"]) if packing_id else None
            facts = db.execute("""SELECT * FROM execute_export_fact WHERE order_id = ?
                ORDER BY sequence DESC LIMIT 1""", (shipment["order_id"] if shipment else "",)).fetchone()
            current = (current and freight is not None and row["freight_sequence"] == freight["sequence"]
                       and packing is not None and packing["status"] == "REVIEWED_SYNTHETIC"
                       and payload["packing_revision"] == packing["revision"]
                       and payload["packing_sha256"] == packing["payload_sha256"]
                       and facts is not None and payload["export_fact_sequence"] == facts["sequence"]
                       and facts["order_sha256"] == row["order_sha256"])
        decision = db.execute("""SELECT decision FROM execute_shipment_document_decision
            WHERE document_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
            (document_id, row["revision"])).fetchone()
        missing = ([key for key in ("exporter_legal_id", "buyer_legal_id", "tax_review_ref",
            "destination_review_ref", "hs_code", "classification_evidence_ref",
            "origin_country_code", "origin_evidence_ref", "manufacturer_review_ref")
            if not payload.get(key)] if row["kind"] == "INVOICE" else [])
        result["missing_review_inputs"] = missing
        result["status"] = ("REVIEW_REQUIRED" if not current or missing else
            "REVIEWED_SYNTHETIC" if decision and decision["decision"] == "REVIEW" else
            "REVOKED" if decision and decision["decision"] == "REVOKE" else "DRAFT")
        result["issued_original"] = False
        return result

    def read_document(self, document_id: str) -> dict | None:
        self.store._require_access("READ_EXECUTE", document_id)
        with closing(self.store._connect()) as db:
            return self._document(db, document_id)

    def _save(self, shipment_id: str, kind: str, payload: dict, freight_sequence: int,
              document_id: str | None, expected_revision: int) -> tuple[str, int]:
        actor = self.store._require_access("EDIT_EXECUTE", shipment_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Expected document revision must be nonnegative")
        with self.store._transaction() as db:
            shipment, order = self._shipment(db, shipment_id)
            freight = self._freight(db, shipment_id)
            if freight is None or freight["sequence"] != freight_sequence:
                raise ValueError("Freight changed; reload shipment")
            if kind == "INVOICE":
                packing_id = db.execute("""SELECT id FROM execute_shipment_document
                    WHERE shipment_id = ? AND kind = 'PACKING'""", (shipment_id,)).fetchone()
                packing = self._document(db, packing_id["id"]) if packing_id else None
                facts = db.execute("""SELECT * FROM execute_export_fact WHERE order_id = ?
                    ORDER BY sequence DESC LIMIT 1""", (shipment["order_id"],)).fetchone()
                if (packing is None or packing["status"] != "REVIEWED_SYNTHETIC"
                        or packing["revision"] != payload["packing_revision"]
                        or packing["payload_sha256"] != payload["packing_sha256"]
                        or facts is None or facts["sequence"] != payload["export_fact_sequence"]):
                    raise ValueError("Reviewed packing and current export facts required")
            existing = db.execute("""SELECT id FROM execute_shipment_document
                WHERE shipment_id = ? AND kind = ?""", (shipment_id, kind)).fetchone()
            if existing is None:
                if document_id or expected_revision:
                    raise ValueError("Document does not exist")
                document_id = f"ESD-{uuid4()}"
                db.execute("""INSERT INTO execute_shipment_document
                    (id, shipment_id, kind, actor_id, created_at_utc) VALUES (?, ?, ?, ?, ?)""",
                    (document_id, shipment_id, kind, actor, _utc_now()))
                revision = 1
            else:
                if existing["id"] != document_id:
                    raise ValueError("Use existing document identity")
                latest = db.execute("""SELECT MAX(revision) FROM execute_shipment_document_revision
                    WHERE document_id = ?""", (document_id,)).fetchone()[0]
                if latest != expected_revision:
                    raise ValueError("Document revision changed; reload")
                revision = latest + 1
            db.execute("""INSERT INTO execute_shipment_document_revision
                (document_id, revision, order_sha256, freight_sequence, payload_json,
                 payload_sha256, actor_id, created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (document_id, revision, order["payload_sha256"], freight_sequence,
                 _json(payload), _digest(payload), actor, _utc_now()))
            return document_id, revision

    def packing(self, shipment_id: str, *, packages: list[dict], marks: str,
                document_id: str | None = None, expected_revision: int = 0) -> tuple[str, int]:
        if not isinstance(packages, list) or not 0 < len(packages) <= 100:
            raise ValueError("Packing packages required")
        totals = {key: Decimal(0) for key in ("quantity", "net_weight_kg", "gross_weight_kg")}
        dimensions = []
        for item in packages:
            if not isinstance(item, dict) or set(item) != {*totals, "dimensions"}:
                raise ValueError("Package fields incomplete")
            for key in totals:
                value = _amount(item[key])
                if value is None or (key == "quantity" and value != value.to_integral_value()):
                    raise ValueError("Package quantity or weight invalid")
                totals[key] += value
            if _amount(item["gross_weight_kg"]) < _amount(item["net_weight_kg"]):
                raise ValueError("Package gross weight below net")
            dimensions.append(_text(item["dimensions"], "dimensions", 300))
        with closing(self.store._connect()) as db:
            shipment, order = self._shipment(db, shipment_id)
            freight = self._freight(db, shipment_id)
            if freight is None or (totals["quantity"] != Decimal(shipment["quantity"])
                    or totals["net_weight_kg"] != Decimal(freight["payload"]["net_weight_kg"])
                    or totals["gross_weight_kg"] != Decimal(freight["payload"]["gross_weight_kg"])
                    or len(packages) != freight["payload"]["packages"]
                    or "; ".join(dimensions) != freight["payload"]["dimensions"]):
                raise ValueError("Packing must reconcile to shipment and freight")
            payload = {"type": "PACKING_LIST_DRAFT", "shipment_id": shipment_id,
                       "order_id": shipment["order_id"], "po_id": order["payload"]["po_id"],
                       "quantity": shipment["quantity"], "unit": order["payload"]["unit"],
                       "sku": order["payload"]["sku"], "packages": packages,
                       "package_count": len(packages), "marks": _text(marks, "marks", 300),
                       "net_weight_kg": str(totals["net_weight_kg"]),
                       "gross_weight_kg": str(totals["gross_weight_kg"]),
                       "destination": freight["payload"]["delivery"],
                       "issued_original": False, "data_origin": "SYNTHETIC"}
        return self._save(shipment_id, "PACKING", payload, freight["sequence"],
                          document_id, expected_revision)

    def invoice(self, shipment_id: str, *, exporter_legal_id: str,
                buyer_legal_id: str, tax_review_ref: str, destination_review_ref: str,
                document_id: str | None = None, expected_revision: int = 0) -> tuple[str, int]:
        legal = {key: _text(value, key, 300) for key, value in (
            ("exporter_legal_id", exporter_legal_id), ("buyer_legal_id", buyer_legal_id),
            ("tax_review_ref", tax_review_ref),
            ("destination_review_ref", destination_review_ref))}
        if not destination_review_ref.startswith("SYN-"):
            raise ValueError("Only synthetic destination review is supported")
        with closing(self.store._connect()) as db:
            shipment, order = self._shipment(db, shipment_id)
            freight = self._freight(db, shipment_id)
            packing_id = db.execute("""SELECT id FROM execute_shipment_document
                WHERE shipment_id = ? AND kind = 'PACKING'""", (shipment_id,)).fetchone()
            packing = self._document(db, packing_id["id"]) if packing_id else None
            facts = db.execute("""SELECT * FROM execute_export_fact WHERE order_id = ?
                ORDER BY sequence DESC LIMIT 1""", (shipment["order_id"],)).fetchone()
            if freight is None or packing is None or packing["status"] != "REVIEWED_SYNTHETIC" or facts is None:
                raise ValueError("Current freight, reviewed packing and export facts required")
            po = order["payload"]
            total = Decimal(shipment["quantity"]) * Decimal(po["unit_price"])
            payload = {"type": "COMMERCIAL_INVOICE_DRAFT", "shipment_id": shipment_id,
                       "order_id": shipment["order_id"], "po_id": po["po_id"],
                       "buyer": po["buyer"], "seller": po["seller"],
                       "description": po["description"], "sku": po["sku"],
                       "quantity": shipment["quantity"], "unit": po["unit"],
                       "unit_price": po["unit_price"], "currency": po["currency"],
                       "total": str(total.quantize(Decimal("0.01"))),
                       "incoterm_code": po["incoterm_code"],
                       "incoterm_place": po["incoterm_place"], **legal,
                       "hs_code": facts["hs_code"],
                       "classification_evidence_ref": facts["classification_evidence_ref"],
                       "origin_country_code": facts["origin_country_code"],
                       "origin_evidence_ref": facts["origin_evidence_ref"],
                       "manufacturer_review_ref": facts["manufacturer_review_ref"],
                       "export_fact_sequence": facts["sequence"],
                       "packing_revision": packing["revision"],
                       "packing_sha256": packing["payload_sha256"],
                       "net_weight_kg": packing["payload"]["net_weight_kg"],
                       "gross_weight_kg": packing["payload"]["gross_weight_kg"],
                       "issued_original": False, "data_origin": "SYNTHETIC"}
        return self._save(shipment_id, "INVOICE", payload, freight["sequence"],
                          document_id, expected_revision)

    def review_document(self, document_id: str, revision: int, *, decision: str,
                        expected_payload_sha256: str, reason: str) -> int:
        actor = self.store._require_access("APPROVE_EXECUTE", document_id)
        if decision not in ("REVIEW", "REVOKE"):
            raise ValueError("Document decision must review or revoke")
        reason = _text(reason, "review reason", 2000)
        with self.store._transaction() as db:
            doc = self._document(db, document_id)
            if (doc is None or doc["revision"] != revision
                    or doc["payload_sha256"] != expected_payload_sha256):
                raise ValueError("Current exact shipment document required")
            if ((decision == "REVIEW" and doc["status"] not in ("DRAFT", "REVOKED"))
                    or (decision == "REVOKE" and doc["status"] != "REVIEWED_SYNTHETIC")):
                raise ValueError("Shipment document cannot be reviewed in current state")
            cur = db.execute("""INSERT INTO execute_shipment_document_decision
                (document_id, revision, decision, payload_sha256, reason, actor_id, reviewed_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?)""", (document_id, revision, decision,
                expected_payload_sha256, reason, actor, _utc_now()))
            return cur.lastrowid

    def confirm_booking(self, shipment_id: str, *, operation_key: str,
                        confirmation_ref: str, source_ref: str,
                        confirmed_at_utc: str, outcome: str = "CONFIRMED_SYNTHETIC") -> str:
        actor = self.store._require_access("EDIT_EXECUTE", shipment_id)
        key = _text(operation_key, "operation key", 120)
        confirmation = _text(confirmation_ref, "confirmation", 300)
        source = _text(source_ref, "carrier evidence", 300)
        if (not confirmation.startswith("SYN-") or not source.startswith("SYN-")
                or outcome not in ("CONFIRMED_SYNTHETIC", "UNKNOWN")):
            raise ValueError("Only synthetic or unknown carrier observation supported")
        at = _time(confirmed_at_utc)
        with self.store._transaction() as db:
            prior = db.execute("SELECT * FROM execute_shipment_booking WHERE shipment_id = ? OR operation_key = ?",
                               (shipment_id, key)).fetchone()
            if prior:
                old = json.loads(prior["payload_json"])
                if (prior["shipment_id"] != shipment_id or prior["operation_key"] != key
                        or any(old[k] != v for k, v in (("status", outcome),
                            ("confirmation_ref", confirmation), ("source_ref", source),
                            ("confirmed_at_utc", at)))):
                    raise ValueError("Booking already observed or operation key conflicts")
                return shipment_id
            shipment, _ = self._shipment(db, shipment_id)
            self._ready(db, shipment["order_id"], Decimal(shipment["quantity"]))
            freight = self._freight(db, shipment_id)
            packing_id = db.execute("""SELECT id FROM execute_shipment_document
                WHERE shipment_id = ? AND kind = 'PACKING'""", (shipment_id,)).fetchone()
            packing = self._document(db, packing_id["id"]) if packing_id else None
            if (freight is None or packing is None or packing["status"] != "REVIEWED_SYNTHETIC"
                    or packing["freight_sequence"] != freight["sequence"]):
                raise ValueError("Current reviewed packing and freight required")
            payload = {"status": outcome, "confirmation_ref": confirmation,
                       "source_ref": source, "confirmed_at_utc": at,
                       "shipment_id": shipment_id, "quantity": shipment["quantity"],
                       "packing_document_id": packing["document_id"],
                       "packing_revision": packing["revision"],
                       "packing_sha256": packing["payload_sha256"],
                       "freight_sequence": freight["sequence"],
                       "real_carrier_booking": False, "data_origin": "SYNTHETIC"}
            db.execute("""INSERT INTO execute_shipment_booking
                (shipment_id, operation_key, payload_json, payload_sha256, actor_id, recorded_at_utc)
                VALUES (?, ?, ?, ?, ?, ?)""", (shipment_id, key, _json(payload),
                _digest(payload), actor, _utc_now()))
            return shipment_id

    def read(self, shipment_id: str) -> dict:
        self.store._require_access("READ_EXECUTE", shipment_id)
        with closing(self.store._connect()) as db:
            shipment, order = self._shipment(db, shipment_id)
            docs = {}
            for row in db.execute("SELECT id, kind FROM execute_shipment_document WHERE shipment_id = ?",
                                  (shipment_id,)):
                docs[row["kind"].lower()] = self._document(db, row["id"])
            booking = db.execute("SELECT * FROM execute_shipment_booking WHERE shipment_id = ?",
                                 (shipment_id,)).fetchone()
            booking_view = None
            if booking:
                booking_view = dict(booking)
                booking_view["payload"] = json.loads(booking_view.pop("payload_json"))
                packing = docs.get("packing")
                freight = self._freight(db, shipment_id)
                booking_view["status"] = ("CURRENT_SYNTHETIC_OBSERVATION"
                    if (packing is not None and packing["status"] == "REVIEWED_SYNTHETIC"
                        and packing["revision"] == booking_view["payload"]["packing_revision"]
                        and packing["payload_sha256"] == booking_view["payload"]["packing_sha256"]
                        and freight is not None
                        and freight["sequence"] == booking_view["payload"]["freight_sequence"])
                    else "REVIEW_REQUIRED")
            return {"shipment": shipment, "order_status": order["status"],
                    "freight": self._freight(db, shipment_id), "documents": docs,
                    "booking": booking_view, "physical_shipment_completed": False,
                    "real_shipped_quantity": "0", "data_origin": "SYNTHETIC"}

    def summary(self, order_id: str) -> dict:
        self.store._require_access("READ_EXECUTE", order_id)
        with closing(self.store._connect()) as db:
            order = self.operations.orders._order(db, order_id)
            if order is None:
                raise ValueError("Order does not exist")
            rows = list(db.execute("SELECT id, quantity FROM execute_shipment WHERE order_id = ?",
                                   (order_id,)))
            allocated = sum((Decimal(row["quantity"]) for row in rows), Decimal(0))
            return {"order_id": order_id, "order_status": order["status"],
                    "ordered_quantity": order["payload"]["quantity"],
                    "allocated_quantity": str(allocated),
                    "unallocated_quantity": str(Decimal(order["payload"]["quantity"]) - allocated),
                    "shipments": [{"id": row["id"], "quantity": row["quantity"]} for row in rows],
                    "real_shipped_quantity": "0", "physical_shipment_completed": False,
                    "data_origin": "SYNTHETIC"}
