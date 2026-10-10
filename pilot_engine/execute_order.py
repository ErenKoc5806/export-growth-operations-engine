"""Review a synthetic customer PO against the current Sell quotation."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from pilot_engine.manual_contact import _text, _time
from pilot_engine.quotations import Quotations
from pilot_engine.store import PilotStore, _json, _utc_now


FIELDS = frozenset({"buyer", "seller", "sku", "description", "quantity", "unit",
                    "unit_price", "currency", "total", "incoterm_code", "incoterm_place",
                    "requested_delivery_at_utc"})
MATCH_FIELDS = ("buyer", "seller", "sku", "quantity", "unit", "unit_price",
                "currency", "total", "incoterm_code", "incoterm_place")


def _amount(value: object) -> Decimal | None:
    if not isinstance(value, str):
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None
    return amount if amount.is_finite() and amount > 0 else None


def _validate(payload: dict) -> None:
    if not isinstance(payload, dict) or set(payload) - FIELDS:
        raise ValueError("Unknown PO fields")
    for key, value in payload.items():
        if (not isinstance(value, str) or not value.strip() or value != value.strip()
                or len(value) > 1000):
            raise ValueError(f"Invalid PO {key}")
    if payload.get("requested_delivery_at_utc"):
        _time(payload["requested_delivery_at_utc"])


class ExecuteOrder:
    def __init__(self, store: PilotStore):
        self.store = store
        self.quotes = Quotations(store)

    def _expected(self, db: object, quotation_id: str) -> tuple[dict, dict]:
        quote = self.quotes._read(db, quotation_id)
        if quote is None:
            raise ValueError("Quotation does not exist")
        party = db.execute("""SELECT c.name AS buyer, m.name AS seller
            FROM sell_quotation q JOIN sell_rfq r ON r.id = q.rfq_id
            JOIN sell_opportunity o ON o.id = r.opportunity_id
            JOIN buyer_candidate c ON c.id = o.candidate_id
            JOIN product p ON p.id = o.product_id
            JOIN manufacturer m ON m.id = p.manufacturer_id
            WHERE q.id = ?""", (quotation_id,)).fetchone()
        expected = {**{key: quote["payload"][key] for key in
                       ("sku", "quantity", "unit", "unit_price", "currency", "total",
                        "incoterm_code", "incoterm_place")},
                    "buyer": party["buyer"], "seller": party["seller"]}
        return quote, expected

    @staticmethod
    def _differences(payload: dict, expected: dict) -> list[str]:
        result = [key for key in FIELDS if not payload.get(key)]
        for key in MATCH_FIELDS:
            if key in result:
                continue
            if key in ("quantity", "unit_price", "total"):
                if _amount(payload[key]) is None or _amount(payload[key]) != _amount(expected[key]):
                    result.append(key)
            elif payload[key] != expected[key]:
                result.append(key)
        quantity, price, total = (_amount(payload.get(k)) for k in
                                  ("quantity", "unit_price", "total"))
        if price and price.as_tuple().exponent < -2:
            result.append("currency_precision")
        if total and total.as_tuple().exponent < -2:
            result.append("total_precision")
        if quantity and price and total and quantity * price != total:
            result.append("line_total")
        if payload.get("unit") == "PCS" and quantity and quantity != quantity.to_integral_value():
            result.append("whole_pieces")
        return sorted(set(result))

    def save(self, quotation_id: str, *, source_ref: str, original_ref: str,
             original: bytes, received_at_utc: str, payload: dict,
             po_id: str | None = None, expected_revision: int = 0) -> tuple[str, int]:
        actor = self.store._require_access("EDIT_EXECUTE", quotation_id)
        source_ref = _text(source_ref, "PO source", 300)
        original_ref = _text(original_ref, "PO original reference", 1000)
        received_at_utc = _time(received_at_utc)
        if (not source_ref.startswith("SYN-") or not original_ref.startswith("SYN-")
                or not isinstance(original, bytes) or not 0 < len(original) <= 20_000_000):
            raise ValueError("Only labeled synthetic PO evidence is allowed")
        _validate(payload)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Expected PO revision must be nonnegative")
        original_sha = hashlib.sha256(original).hexdigest()
        payload_sha = hashlib.sha256(_json(payload).encode()).hexdigest()
        with self.store._transaction() as db:
            quote, _ = self._expected(db, quotation_id)
            if quote["status"] != "APPROVED_SYNTHETIC":
                raise ValueError("Current approved synthetic quotation is required")
            existing = db.execute("SELECT * FROM execute_po WHERE source_ref = ?",
                                  (source_ref,)).fetchone()
            if existing is None:
                if po_id or expected_revision:
                    raise ValueError("PO does not exist")
                po_id = f"PO-{uuid4()}"
                db.execute("""INSERT INTO execute_po
                    (id, quotation_id, source_ref, original_ref, original_sha256,
                     received_at_utc, data_origin, actor_id, created_at_utc)
                    VALUES (?, ?, ?, ?, ?, ?, 'SYNTHETIC', ?, ?)""",
                    (po_id, quotation_id, source_ref, original_ref, original_sha,
                     received_at_utc, actor, _utc_now()))
                revision = 1
            else:
                if (existing["quotation_id"], existing["original_ref"],
                        existing["original_sha256"], existing["received_at_utc"]) != (
                        quotation_id, original_ref, original_sha, received_at_utc):
                    raise ValueError("PO source reference conflicts with original evidence")
                if po_id is None and expected_revision == 0:
                    last = db.execute("""SELECT revision, payload_sha256 FROM execute_po_revision
                        WHERE po_id = ? ORDER BY revision DESC LIMIT 1""", (existing["id"],)).fetchone()
                    if last["payload_sha256"] == payload_sha:
                        return existing["id"], last["revision"]
                    raise ValueError("Use existing PO identity and revision for correction")
                if po_id != existing["id"]:
                    raise ValueError("Use existing PO identity")
                last = db.execute("SELECT MAX(revision) FROM execute_po_revision WHERE po_id = ?",
                                  (po_id,)).fetchone()[0]
                if last != expected_revision:
                    raise ValueError("PO revision changed; reload")
                if db.execute("SELECT 1 FROM execute_local_order WHERE po_id = ?",
                              (po_id,)).fetchone():
                    raise ValueError("Approved order cannot be silently replaced")
                revision = last + 1
            db.execute("""INSERT INTO execute_po_revision
                (po_id, revision, quotation_revision, payload_json, payload_sha256,
                 actor_id, created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (po_id, revision, quote["revision"], _json(payload), payload_sha,
                 actor, _utc_now()))
            return po_id, revision

    def _read(self, db: object, po_id: str) -> dict | None:
        row = db.execute("""SELECT p.*, r.revision, r.quotation_revision,
            r.payload_json, r.payload_sha256 FROM execute_po p
            JOIN execute_po_revision r ON r.po_id = p.id WHERE p.id = ?
            ORDER BY r.revision DESC LIMIT 1""", (po_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        quote, expected = self._expected(db, row["quotation_id"])
        result["differences"] = self._differences(result["payload"], expected)
        result["quote_current"] = (quote["status"] == "APPROVED_SYNTHETIC"
                                   and quote["revision"] == row["quotation_revision"])
        decision = db.execute("""SELECT decision FROM execute_po_decision
            WHERE po_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
            (po_id, row["revision"])).fetchone()
        order = db.execute("SELECT po_revision, payload_json FROM execute_local_order WHERE po_id = ?",
                           (po_id,)).fetchone()
        result["historical_order_approval"] = (order is not None and
                                               order["po_revision"] == row["revision"] and
                                               json.loads(order["payload_json"]).get(
                                                   "po_payload_sha256", row["payload_sha256"]) ==
                                               row["payload_sha256"])
        if decision and decision["decision"] == "REVOKE":
            result["status"] = "REVOKED"
        elif decision and decision["decision"] == "APPROVE" and result["historical_order_approval"]:
            result["status"] = "APPROVED_SYNTHETIC"
        elif result["differences"] or not result["quote_current"]:
            result["status"] = "REVIEW_REQUIRED"
        elif decision and decision["decision"] == "APPROVE":
            result["status"] = "APPROVED_SYNTHETIC"
        else:
            result["status"] = "DRAFT"
        result["real_customer_po"] = False
        return result

    def read(self, po_id: str) -> dict | None:
        self.store._require_access("READ_EXECUTE", po_id)
        with closing(self.store._connect()) as db:
            return self._read(db, po_id)

    def decide(self, po_id: str, revision: int, decision: str, *,
               expected_payload_sha256: str, reason: str) -> str | int:
        actor = self.store._require_access("APPROVE_EXECUTE", po_id)
        reason = _text(reason, "PO decision reason", 2000)
        if decision not in ("APPROVE", "REVOKE"):
            raise ValueError("Unknown PO decision")
        with self.store._transaction() as db:
            po = self._read(db, po_id)
            if po is None or po["revision"] != revision or po["payload_sha256"] != expected_payload_sha256:
                raise ValueError("Current exact PO revision is required")
            existing_order = db.execute("SELECT * FROM execute_local_order WHERE po_id = ?",
                                        (po_id,)).fetchone()
            if decision == "APPROVE":
                if po["status"] == "APPROVED_SYNTHETIC" and existing_order:
                    return existing_order["id"]
                if po["status"] not in ("DRAFT", "REVOKED") or existing_order:
                    raise ValueError("PO mismatch, stale quote or prior order requires review")
            elif po["status"] != "APPROVED_SYNTHETIC":
                raise ValueError("Only an effective approval can be revoked")
            cur = db.execute("""INSERT INTO execute_po_decision
                (po_id, revision, decision, payload_sha256, reason, actor_id, decided_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (po_id, revision, decision, expected_payload_sha256, reason,
                 actor, _utc_now()))
            if decision == "REVOKE":
                return cur.lastrowid
            quote_approval = db.execute("""SELECT decision, decided_at_utc FROM sell_quotation_decision
                WHERE quotation_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
                (po["quotation_id"], po["quotation_revision"])).fetchone()
            if quote_approval is None or quote_approval["decision"] != "APPROVE":
                raise ValueError("Effective quotation approval required")
            order_id = f"EO-{uuid4()}"
            snapshot = {**po["payload"], "po_id": po_id, "po_revision": revision,
                        "po_payload_sha256": po["payload_sha256"],
                        "quotation_id": po["quotation_id"],
                        "quotation_revision": po["quotation_revision"],
                        "quote_approved_at_utc": quote_approval["decided_at_utc"]}
            digest = hashlib.sha256(_json(snapshot).encode()).hexdigest()
            db.execute("""INSERT INTO execute_local_order
                (id, po_id, po_revision, payload_json, payload_sha256, actor_id, created_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (order_id, po_id, revision, _json(snapshot), digest, actor, _utc_now()))
            return order_id

    def _order(self, db: object, order_id: str) -> dict | None:
        row = db.execute("SELECT * FROM execute_local_order WHERE id = ?",
                         (order_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        po = self._read(db, row["po_id"])
        result["status"] = ("LOCAL_ORDER_SYNTHETIC" if po["status"] == "APPROVED_SYNTHETIC"
                            and po["revision"] == row["po_revision"] else "REVIEW_REQUIRED")
        result["erp_created"] = False
        return result

    def read_order(self, order_id: str) -> dict | None:
        self.store._require_access("READ_EXECUTE", order_id)
        with closing(self.store._connect()) as db:
            return self._order(db, order_id)
