"""Versioned synthetic export-document drafts and case reconciliation."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from decimal import Decimal
from uuid import uuid4

from pilot_engine.execute_operations import ExecuteOperations
from pilot_engine.execute_order import _amount
from pilot_engine.manual_contact import _text
from pilot_engine.store import PilotStore, _json, _utc_now


class ExecuteDocuments:
    def __init__(self, store: PilotStore):
        self.store = store
        self.operations = ExecuteOperations(store)

    def _save(self, order_id: str, kind: str, payload: dict, *,
              freight_sequence: int | None = None,
              document_id: str | None = None, expected_revision: int = 0) -> tuple[str, int]:
        actor = self.store._require_access("EDIT_EXECUTE", order_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Expected document revision must be nonnegative")
        with self.store._transaction() as db:
            order = self.operations.orders._order(db, order_id)
            if order is None or order["status"] != "LOCAL_ORDER_SYNTHETIC":
                raise ValueError("Current approved order required")
            existing = db.execute("SELECT id FROM execute_document WHERE order_id = ? AND kind = ?",
                                  (order_id, kind)).fetchone()
            if existing is None:
                if document_id or expected_revision:
                    raise ValueError("Document does not exist")
                document_id = f"ED-{uuid4()}"
                db.execute("""INSERT INTO execute_document
                    (id, order_id, kind, actor_id, created_at_utc) VALUES (?, ?, ?, ?, ?)""",
                    (document_id, order_id, kind, actor, _utc_now()))
                revision = 1
            else:
                if document_id != existing["id"]:
                    raise ValueError("Use existing document identity")
                latest = db.execute("""SELECT MAX(revision) FROM execute_document_revision
                    WHERE document_id = ?""", (document_id,)).fetchone()[0]
                if latest != expected_revision:
                    raise ValueError("Document revision changed; reload")
                revision = latest + 1
            digest = hashlib.sha256(_json(payload).encode()).hexdigest()
            db.execute("""INSERT INTO execute_document_revision
                (document_id, revision, order_sha256, freight_sequence,
                 payload_json, payload_sha256, actor_id, created_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (document_id, revision, order["payload_sha256"], freight_sequence,
                 _json(payload), digest, actor, _utc_now()))
            return document_id, revision

    def invoice(self, order_id: str, *, exporter_legal_id: str | None,
                buyer_legal_id: str | None, tax_review_ref: str | None,
                document_id: str | None = None, expected_revision: int = 0) -> tuple[str, int]:
        self.store._require_access("EDIT_EXECUTE", order_id)
        with closing(self.store._connect()) as db:
            order = self.operations.orders._order(db, order_id)
            if order is None:
                raise ValueError("Order does not exist")
            po = order["payload"]
            source = db.execute("SELECT source_ref FROM execute_po WHERE id = ?",
                                (po["po_id"],)).fetchone()[0]
        for value in (exporter_legal_id, buyer_legal_id, tax_review_ref):
            if value is not None:
                _text(value, "invoice legal/tax reference", 300)
        payload = {"type": "COMMERCIAL_INVOICE_DRAFT", "order_id": order_id,
                   "po_id": po["po_id"], "po_source_ref": source,
                   "buyer": po["buyer"], "seller": po["seller"],
                   "description": po["description"], "sku": po["sku"],
                   "quantity": po["quantity"], "unit": po["unit"],
                   "unit_price": po["unit_price"], "currency": po["currency"],
                   "total": po["total"], "incoterm_code": po["incoterm_code"],
                   "incoterm_place": po["incoterm_place"],
                   "exporter_legal_id": exporter_legal_id,
                   "buyer_legal_id": buyer_legal_id, "tax_review_ref": tax_review_ref,
                   "issued_original": False, "data_origin": "SYNTHETIC"}
        return self._save(order_id, "INVOICE", payload,
                          document_id=document_id, expected_revision=expected_revision)

    def packing(self, order_id: str, *, packages: list[dict], marks: str,
                document_id: str | None = None, expected_revision: int = 0) -> tuple[str, int]:
        if not isinstance(packages, list) or not 0 < len(packages) <= 100:
            raise ValueError("Packing packages required")
        _text(marks, "package marks", 300)
        for item in packages:
            if not isinstance(item, dict) or set(item) != {
                    "quantity", "net_weight_kg", "gross_weight_kg", "dimensions"}:
                raise ValueError("Package fields incomplete")
            net, gross, quantity = (_amount(item[k]) for k in
                                    ("net_weight_kg", "gross_weight_kg", "quantity"))
            if (not net or not gross or gross < net or not quantity
                    or quantity != quantity.to_integral_value()):
                raise ValueError("Package quantity or weights inconsistent")
            _text(item["dimensions"], "dimensions", 300)
        self.store._require_access("EDIT_EXECUTE", order_id)
        with closing(self.store._connect()) as db:
            order = self.operations.orders._order(db, order_id)
            freight = self.operations._latest(db, order_id, "FREIGHT_PLAN")
            if order is None or freight is None:
                raise ValueError("Order and freight plan required")
            po = order["payload"]
            total_qty = sum((_amount(x["quantity"]) for x in packages), Decimal(0))
            total_net = sum((_amount(x["net_weight_kg"]) for x in packages), Decimal(0))
            total_gross = sum((_amount(x["gross_weight_kg"]) for x in packages), Decimal(0))
            plan = freight["payload"]
            if (total_qty != Decimal(po["quantity"]) or
                    (plan["packages"] is not None and len(packages) != plan["packages"]) or
                    (plan["net_weight_kg"] is not None and
                     total_net != Decimal(plan["net_weight_kg"])) or
                    (plan["gross_weight_kg"] is not None and
                     total_gross != Decimal(plan["gross_weight_kg"]))):
                raise ValueError("Packing quantity and weights must reconcile to order and freight")
        payload = {"type": "PACKING_LIST_DRAFT", "order_id": order_id,
                   "po_id": po["po_id"], "sku": po["sku"], "unit": po["unit"],
                   "quantity": str(total_qty), "packages": packages,
                   "package_count": len(packages), "marks": marks,
                   "net_weight_kg": str(total_net), "gross_weight_kg": str(total_gross),
                   "destination": plan["delivery"], "issued_original": False,
                   "data_origin": "SYNTHETIC"}
        return self._save(order_id, "PACKING", payload, freight_sequence=freight["sequence"],
                          document_id=document_id, expected_revision=expected_revision)

    def checklist(self, order_id: str, *, items: list[dict],
                  document_id: str | None = None, expected_revision: int = 0) -> tuple[str, int]:
        if not isinstance(items, list) or not items or len(items) > 50:
            raise ValueError("Shipping document checklist required")
        names = set()
        for item in items:
            if not isinstance(item, dict) or set(item) != {"name", "status", "owner", "evidence_ref"}:
                raise ValueError("Checklist fields incomplete")
            name = _text(item["name"], "document name", 120)
            if name in names or item["status"] not in ("REQUIRED", "NOT_REQUIRED", "UNKNOWN"):
                raise ValueError("Duplicate document or unknown checklist status")
            names.add(name)
            _text(item["owner"], "document owner", 300)
            if item["status"] != "UNKNOWN":
                _text(item["evidence_ref"], "checklist evidence", 300)
            if item["evidence_ref"] and not item["evidence_ref"].startswith("SYN-"):
                raise ValueError("Only synthetic checklist evidence is supported")
        payload = {"type": "SHIPPING_DOCUMENT_CHECKLIST", "order_id": order_id,
                   "items": items, "documents_issued": False, "data_origin": "SYNTHETIC"}
        return self._save(order_id, "CHECKLIST", payload,
                          document_id=document_id, expected_revision=expected_revision)

    def _read(self, db: object, document_id: str) -> dict | None:
        row = db.execute("""SELECT d.order_id, d.kind, r.* FROM execute_document d
            JOIN execute_document_revision r ON r.document_id = d.id
            WHERE d.id = ? ORDER BY r.revision DESC LIMIT 1""", (document_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        order = self.operations.orders._order(db, row["order_id"])
        current = (order is not None and order["status"] == "LOCAL_ORDER_SYNTHETIC"
                   and order["payload_sha256"] == row["order_sha256"])
        if row["kind"] == "PACKING":
            freight = self.operations._latest(db, row["order_id"], "FREIGHT_PLAN")
            current = current and freight is not None and freight["sequence"] == row["freight_sequence"]
        decision = db.execute("""SELECT decision FROM execute_document_decision
            WHERE document_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
            (document_id, row["revision"])).fetchone()
        result["missing_review_inputs"] = (
            [key for key in ("exporter_legal_id", "buyer_legal_id", "tax_review_ref")
             if not result["payload"].get(key)] if row["kind"] == "INVOICE" else
            ["unknown_documents"] if row["kind"] == "CHECKLIST" and any(
                x["status"] == "UNKNOWN" for x in result["payload"]["items"]) else [])
        result["status"] = ("REVIEW_REQUIRED" if not current or result["missing_review_inputs"]
                            else "REVIEWED_SYNTHETIC" if decision and decision["decision"] == "REVIEW"
                            else "REVOKED" if decision and decision["decision"] == "REVOKE"
                            else "DRAFT")
        result["issued_original"] = False
        return result

    def read(self, document_id: str) -> dict | None:
        self.store._require_access("READ_EXECUTE", document_id)
        with closing(self.store._connect()) as db:
            return self._read(db, document_id)

    def review(self, document_id: str, revision: int, *, decision: str,
               expected_payload_sha256: str, reason: str) -> int:
        actor = self.store._require_access("APPROVE_EXECUTE", document_id)
        reason = _text(reason, "document review reason", 2000)
        if decision not in ("REVIEW", "REVOKE"):
            raise ValueError("Document decision must review or revoke")
        with self.store._transaction() as db:
            document = self._read(db, document_id)
            if (document is None or document["revision"] != revision or
                    document["payload_sha256"] != expected_payload_sha256):
                raise ValueError("Current exact document revision is required")
            if decision == "REVIEW" and document["status"] not in ("DRAFT", "REVOKED"):
                raise ValueError("Missing legal, packing or checklist review inputs")
            if decision == "REVOKE" and document["status"] != "REVIEWED_SYNTHETIC":
                raise ValueError("Only a reviewed draft can be revoked")
            cur = db.execute("""INSERT INTO execute_document_decision
                (document_id, revision, decision, payload_sha256, reason, actor_id, reviewed_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (document_id, revision, decision, expected_payload_sha256, reason,
                 actor, _utc_now()))
            return cur.lastrowid

    def record_case_metric(self, order_id: str, *, event_key: str, minutes: int,
                           corrections: int, failures: int, evidence_ref: str) -> int:
        actor = self.store._require_access("EDIT_EXECUTE", order_id)
        event_key = _text(event_key, "case metric event key", 120)
        evidence_ref = _text(evidence_ref, "case metric evidence", 300)
        if (not evidence_ref.startswith("SYN-") or
                any(type(value) is not int or value < 0 for value in
                    (minutes, corrections, failures))):
            raise ValueError("Synthetic nonnegative case metrics required")
        with self.store._transaction() as db:
            prior = db.execute("SELECT * FROM execute_case_metric WHERE event_key = ?",
                               (event_key,)).fetchone()
            if prior:
                if (prior["order_id"], prior["minutes"], prior["corrections"],
                        prior["failures"], prior["evidence_ref"]) != (
                        order_id, minutes, corrections, failures, evidence_ref):
                    raise ValueError("Case metric event key conflicts")
                return prior["sequence"]
            if self.operations.orders._order(db, order_id) is None:
                raise ValueError("Order does not exist")
            cur = db.execute("""INSERT INTO execute_case_metric
                (event_key, order_id, minutes, corrections, failures,
                 evidence_ref, actor_id, recorded_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_key, order_id, minutes, corrections, failures,
                 evidence_ref, actor, _utc_now()))
            return cur.lastrowid

    def case_summary(self, order_id: str) -> dict:
        self.store._require_access("READ_EXECUTE", order_id)
        with closing(self.store._connect()) as db:
            operations = self.operations.summary(order_id)
            order = operations["order"]
            po = self.operations.orders._read(db, order["po_id"])
            docs = {}
            for row in db.execute("SELECT id, kind FROM execute_document WHERE order_id = ?",
                                  (order_id,)):
                docs[row["kind"].lower()] = self._read(db, row["id"])
            quote = self.operations.orders.quotes._read(db, po["quotation_id"])
            rfq = self.operations.orders.quotes.rfqs._read(db, quote["rfq_id"])
            path = db.execute("""SELECT o.id AS opportunity_id, h.id AS handoff_id,
                h.route_id, c.id AS candidate_id, c.name AS candidate_name,
                p.id AS product_id, p.sku
                FROM sell_rfq r JOIN sell_opportunity o ON o.id = r.opportunity_id
                JOIN find_handoff h ON h.id = o.originating_handoff_id
                JOIN buyer_candidate c ON c.id = o.candidate_id
                JOIN product p ON p.id = o.product_id WHERE r.id = ?""",
                (quote["rfq_id"],)).fetchone()
            metrics = [dict(row) for row in db.execute("""SELECT * FROM execute_case_metric
                WHERE order_id = ? ORDER BY sequence""", (order_id,))]
            issues = []
            if order["status"] != "LOCAL_ORDER_SYNTHETIC":
                issues.append("order_stale")
            for kind in ("invoice", "packing", "checklist"):
                if kind not in docs or docs[kind]["status"] != "REVIEWED_SYNTHETIC":
                    issues.append(f"{kind}_review_pending")
            if operations["manual_erp_handoff"] is None:
                issues.append("manual_erp_handoff_missing")
            if operations["readiness"] is None:
                issues.append("readiness_observation_missing")
            if (operations["freight_plan"] is None or
                    operations["freight_plan"]["payload"]["status"] != "REQUESTED"):
                issues.append("freight_request_pending")
            return {"order_id": order_id, "po_id": po["id"],
                    "quotation_id": po["quotation_id"], "rfq_id": quote["rfq_id"],
                    "inbound_id": rfq["inbound_id"], "find_to_sell": dict(path),
                    "po": po, "quotation": quote, "documents": docs,
                    "operations": operations, "issues": issues,
                    "technical_case_accepted": not issues,
                    "manufacturer_documents_released": False,
                    "real_freight_booked": False, "physical_shipment_completed": False,
                    "operator_time_minutes": sum(item["minutes"] for item in metrics),
                    "corrections": sum(item["corrections"] for item in metrics),
                    "failures": sum(item["failures"] for item in metrics),
                    "metric_events": metrics,
                    "data_origin": "SYNTHETIC"}
