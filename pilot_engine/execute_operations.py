"""Operator-attested local Execute milestones; no ERP or carrier side effects."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from decimal import Decimal

from pilot_engine.execute_order import ExecuteOrder, _amount
from pilot_engine.manual_contact import _text, _time
from pilot_engine.store import PilotStore, _json, _utc_now


class ExecuteOperations:
    def __init__(self, store: PilotStore):
        self.store = store
        self.orders = ExecuteOrder(store)

    @staticmethod
    def _latest(db: object, order_id: str, kind: str) -> dict | None:
        row = db.execute("""SELECT * FROM execute_operation_event
            WHERE order_id = ? AND kind = ? ORDER BY sequence DESC LIMIT 1""",
            (order_id, kind)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def _record(self, order_id: str, operation_key: str, kind: str, payload: dict) -> int:
        actor = self.store._require_access("EDIT_EXECUTE", order_id)
        operation_key = _text(operation_key, "operation key", 120)
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        with self.store._transaction() as db:
            prior = db.execute("SELECT * FROM execute_operation_event WHERE operation_key = ?",
                               (operation_key,)).fetchone()
            if prior:
                if (prior["order_id"], prior["kind"], prior["payload_sha256"]) != (
                        order_id, kind, digest):
                    raise ValueError("Operation key conflicts with prior event")
                return prior["sequence"]
            order = self.orders._order(db, order_id)
            if order is None or order["status"] != "LOCAL_ORDER_SYNTHETIC":
                raise ValueError("Current approved local order is required")
            if kind == "BOOKING_CONFIRMATION":
                plan = self._latest(db, order_id, "FREIGHT_PLAN")
                readiness = self._latest(db, order_id, "READINESS")
                if (plan is None or plan["payload"]["status"] != "REQUESTED"
                        or readiness is None or readiness["payload"]["status"] != "READY"):
                    raise ValueError("Requested freight plan and confirmed readiness required")
            cur = db.execute("""INSERT INTO execute_operation_event
                (operation_key, order_id, kind, payload_json, payload_sha256,
                 actor_id, recorded_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (operation_key, order_id, kind, _json(payload), digest, actor, _utc_now()))
            return cur.lastrowid

    def manual_erp_handoff(self, order_id: str, *, operation_key: str,
                           customer_mapping_ref: str, tax_mapping_ref: str,
                           handed_to: str, evidence_ref: str) -> int:
        """Prepare an auditable manual handoff, never an ERP-created assertion."""
        payload = {"customer_mapping_ref": _text(customer_mapping_ref, "customer mapping", 300),
                   "tax_mapping_ref": _text(tax_mapping_ref, "tax mapping", 300),
                   "handed_to": _text(handed_to, "handoff owner", 300),
                   "evidence_ref": _text(evidence_ref, "handoff evidence", 1000),
                   "status": "MANUAL_HANDOFF_RECORDED", "erp_created": False}
        if not evidence_ref.startswith("SYN-"):
            raise ValueError("Only synthetic handoff evidence is supported")
        payload["export_preview"] = self.erp_export_preview(
            order_id, customer_mapping_ref=customer_mapping_ref,
            tax_mapping_ref=tax_mapping_ref)
        return self._record(order_id, operation_key, "MANUAL_ERP_HANDOFF", payload)

    def erp_export_preview(self, order_id: str, *, customer_mapping_ref: str,
                           tax_mapping_ref: str) -> dict:
        """Return reviewable fields for manual entry; no ERP request is made."""
        self.store._require_access("READ_EXECUTE", order_id)
        customer_mapping_ref = _text(customer_mapping_ref, "customer mapping", 300)
        tax_mapping_ref = _text(tax_mapping_ref, "tax mapping", 300)
        with closing(self.store._connect()) as db:
            order = self.orders._order(db, order_id)
            if order is None or order["status"] != "LOCAL_ORDER_SYNTHETIC":
                raise ValueError("Current approved local order is required")
            po = order["payload"]
            return {"order_id": order_id, "order_sha256": order["payload_sha256"],
                    "customer_mapping_ref": customer_mapping_ref,
                    "tax_mapping_ref": tax_mapping_ref,
                    "buyer": po["buyer"], "seller": po["seller"],
                    "source_po_id": po["po_id"], "source_quotation_id": po["quotation_id"],
                    "lines": [{"sku": po["sku"], "description": po["description"],
                               "quantity": po["quantity"], "unit": po["unit"],
                               "unit_price": po["unit_price"], "total": po["total"]}],
                    "currency": po["currency"], "total": po["total"],
                    "incoterm_code": po["incoterm_code"],
                    "incoterm_place": po["incoterm_place"],
                    "requested_delivery_at_utc": po["requested_delivery_at_utc"],
                    "erp_created": False, "data_origin": "SYNTHETIC"}

    def readiness(self, order_id: str, *, operation_key: str, status: str,
                  planned_at_utc: str, actual_at_utc: str | None,
                  quantity: str | None, source_ref: str, reason: str,
                  operator_confirmed: bool = False) -> int:
        if status not in ("PENDING", "DELAYED", "UNKNOWN", "PARTIAL", "READY"):
            raise ValueError("Unknown goods-readiness status")
        planned = _time(planned_at_utc)
        actual = _time(actual_at_utc) if actual_at_utc is not None else None
        source = _text(source_ref, "readiness source", 300)
        reason = _text(reason, "readiness reason", 1000)
        if not source.startswith("SYN-"):
            raise ValueError("Only synthetic readiness evidence is supported")
        amount = _amount(quantity) if quantity is not None else None
        if ((status in ("READY", "PARTIAL")) != (actual is not None)
                or (status in ("READY", "PARTIAL")) != (amount is not None)
                or (status in ("READY", "PARTIAL") and operator_confirmed is not True)):
            raise ValueError("Actual readiness needs quantity, date and operator confirmation")
        self.store._require_access("EDIT_EXECUTE", order_id)
        with closing(self.store._connect()) as db:
            order = self.orders._order(db, order_id)
            if order is None:
                raise ValueError("Order does not exist")
            ordered = Decimal(order["payload"]["quantity"])
            if amount and (amount > ordered or
                           (status == "READY" and amount != ordered) or
                           (status == "PARTIAL" and amount >= ordered)):
                raise ValueError("Readiness quantity conflicts with approved order")
        payload = {"status": status, "planned_at_utc": planned, "actual_at_utc": actual,
                   "quantity": str(amount) if amount else None, "source_ref": source,
                   "reason": reason, "operator_confirmed": operator_confirmed is True,
                   "data_origin": "SYNTHETIC"}
        return self._record(order_id, operation_key, "READINESS", payload)

    def plan_freight(self, order_id: str, *, operation_key: str, status: str,
                     pickup: str, delivery: str, packages: int | None,
                     net_weight_kg: str | None, gross_weight_kg: str | None,
                     requested_at_utc: str, dimensions: str | None = None,
                     forwarder_ref: str | None = None) -> int:
        if status not in ("ESTIMATED", "REQUESTED", "UNKNOWN"):
            raise ValueError("Freight plan cannot imply a booking")
        pickup = _text(pickup, "pickup location", 500)
        delivery = _text(delivery, "delivery location", 500)
        requested = _time(requested_at_utc)
        if packages is not None and (type(packages) is not int or packages <= 0):
            raise ValueError("Packages must be a positive whole number")
        net = _amount(net_weight_kg) if net_weight_kg is not None else None
        gross = _amount(gross_weight_kg) if gross_weight_kg is not None else None
        if ((net_weight_kg is not None and net is None)
                or (gross_weight_kg is not None and gross is None)
                or (net and gross and gross < net)):
            raise ValueError("Freight weights are inconsistent")
        if dimensions is not None:
            _text(dimensions, "shipment dimensions", 500)
        if status == "REQUESTED" and (packages is None or net is None or gross is None
                                       or dimensions is None):
            raise ValueError("Request needs reconciled packing, weights and dimensions")
        if forwarder_ref is not None:
            _text(forwarder_ref, "forwarder reference", 300)
        self.store._require_access("EDIT_EXECUTE", order_id)
        with closing(self.store._connect()) as db:
            order = self.orders._order(db, order_id)
            if order is None or delivery != order["payload"]["incoterm_place"]:
                raise ValueError("Delivery place differs from approved order")
            terms = {key: order["payload"][key] for key in ("incoterm_code", "incoterm_place")}
        payload = {"status": status, "pickup": pickup, "delivery": delivery,
                   "packages": packages, "net_weight_kg": str(net) if net else None,
                   "gross_weight_kg": str(gross) if gross else None,
                   "dimensions": dimensions,
                   "requested_at_utc": requested, "forwarder_ref": forwarder_ref,
                   **terms, "carrier_booked": False, "data_origin": "SYNTHETIC"}
        return self._record(order_id, operation_key, "FREIGHT_PLAN", payload)

    def confirm_booking(self, order_id: str, *, operation_key: str,
                        confirmation_ref: str, source_ref: str,
                        confirmed_at_utc: str) -> int:
        confirmation = _text(confirmation_ref, "carrier confirmation", 300)
        source = _text(source_ref, "carrier evidence", 300)
        if not confirmation.startswith("SYN-") or not source.startswith("SYN-"):
            raise ValueError("Only synthetic carrier evidence is supported")
        payload = {"status": "CONFIRMED_SYNTHETIC", "confirmation_ref": confirmation,
                   "source_ref": source, "confirmed_at_utc": _time(confirmed_at_utc),
                   "real_carrier_booking": False}
        return self._record(order_id, operation_key, "BOOKING_CONFIRMATION", payload)

    def summary(self, order_id: str) -> dict:
        self.store._require_access("READ_EXECUTE", order_id)
        with closing(self.store._connect()) as db:
            order = self.orders._order(db, order_id)
            if order is None:
                raise ValueError("Order does not exist")
            result = {"order": order}
            for kind in ("MANUAL_ERP_HANDOFF", "READINESS", "FREIGHT_PLAN",
                         "BOOKING_CONFIRMATION"):
                result[kind.lower()] = self._latest(db, order_id, kind)
            result["erp_created"] = False
            result["physical_shipment_completed"] = False
            return result
