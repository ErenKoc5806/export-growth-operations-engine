"""Versioned synthetic RFQ extraction from a reviewed inbound request."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from pilot_engine.find_handoff import FindHandoff
from pilot_engine.manual_contact import _text, _time
from pilot_engine.store import PilotStore, _json, _utc_now


FIELDS = {"customer_reference", "sku", "specification", "quantity", "unit",
          "destination", "requested_terms", "response_due_at_utc", "resolution_note"}
REQUIRED = ("sku", "specification", "quantity", "unit", "destination")


def _validate(payload: dict) -> list[str]:
    if not isinstance(payload, dict) or set(payload) - FIELDS:
        raise ValueError("Unknown RFQ fields")
    for key in FIELDS - {"requested_terms"}:
        value = payload.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 2000
                                  or value != value.strip()):
            raise ValueError(f"Invalid {key}")
    terms = payload.get("requested_terms", {})
    if not isinstance(terms, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                          or len(k) > 100 or len(v) > 1000
                                          for k, v in terms.items()):
        raise ValueError("Invalid requested terms")
    if payload.get("response_due_at_utc"):
        _time(payload["response_due_at_utc"])
    if payload.get("quantity"):
        try:
            quantity = Decimal(payload["quantity"])
        except (InvalidOperation, TypeError) as exc:
            raise ValueError("Invalid RFQ quantity") from exc
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError("Invalid RFQ quantity")
    return [key for key in REQUIRED if not payload.get(key)]


class SellRFQ:
    def __init__(self, store: PilotStore):
        self.store = store

    @staticmethod
    def _source(db: object, inbound_id: str) -> tuple[object, object]:
        inbound = db.execute("SELECT * FROM sell_inbound_message WHERE id = ?",
                             (inbound_id,)).fetchone()
        review = db.execute("""SELECT * FROM sell_inbound_review WHERE inbound_id = ?
            ORDER BY sequence DESC LIMIT 1""", (inbound_id,)).fetchone()
        if (inbound is None or inbound["data_origin"] != "SYNTHETIC" or review is None
                or review["classification"] != "RFQ_CANDIDATE"):
            raise ValueError("Reviewed synthetic inbound RFQ request is required")
        return inbound, review

    def save(self, inbound_id: str, payload: dict, *, rfq_id: str | None = None,
             expected_revision: int = 0) -> tuple[str, int]:
        actor = self.store._require_access("EDIT_SELL_RFQ", inbound_id)
        missing = _validate(payload)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Expected RFQ revision must be nonnegative")
        digest = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
        with self.store._transaction() as db:
            inbound, review = self._source(db, inbound_id)
            existing = db.execute("SELECT * FROM sell_rfq WHERE inbound_id = ?",
                                  (inbound_id,)).fetchone()
            if existing is None:
                if rfq_id or expected_revision != 0:
                    raise ValueError("RFQ does not exist")
                rfq_id = f"RF-{uuid4()}"
                db.execute("""INSERT INTO sell_rfq
                    (id, inbound_id, opportunity_id, actor_id, created_at_utc)
                    VALUES (?, ?, ?, ?, ?)""",
                    (rfq_id, inbound_id, review["opportunity_id"], actor, _utc_now()))
                revision = 1
            else:
                if rfq_id != existing["id"]:
                    raise ValueError("Use the existing RFQ identity")
                current = db.execute("SELECT MAX(revision) FROM sell_rfq_revision WHERE rfq_id = ?",
                                     (rfq_id,)).fetchone()[0]
                if current != expected_revision:
                    raise ValueError("RFQ revision changed; reload before editing")
                if review["opportunity_id"] != existing["opportunity_id"]:
                    raise ValueError("Inbound opportunity changed; review RFQ linkage")
                revision = current + 1
            db.execute("""INSERT INTO sell_rfq_revision
                (rfq_id, revision, inbound_review_sequence, payload_json,
                 payload_sha256, missing_json, actor_id, created_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (rfq_id, revision, review["sequence"], _json(payload), digest,
                 _json(missing), actor, _utc_now()))
            return rfq_id, revision

    def decide(self, rfq_id: str, revision: int, decision: str, reason: str) -> int:
        actor = self.store._require_access("APPROVE_SELL_RFQ", rfq_id)
        reason = _text(reason, "RFQ decision reason", 2000)
        if decision not in ("ACCEPT", "REVOKE"):
            raise ValueError("RFQ decision must accept or revoke")
        with self.store._transaction() as db:
            row = db.execute("""SELECT q.inbound_id, q.opportunity_id, r.* FROM sell_rfq q
                JOIN sell_rfq_revision r ON r.rfq_id = q.id
                WHERE q.id = ? AND r.revision = ?""", (rfq_id, revision)).fetchone()
            latest = db.execute("SELECT MAX(revision) FROM sell_rfq_revision WHERE rfq_id = ?",
                                (rfq_id,)).fetchone()[0]
            if row is None or latest != revision:
                raise ValueError("Current RFQ revision is required")
            prior = db.execute("""SELECT decision FROM sell_rfq_decision WHERE rfq_id = ?
                AND revision = ? ORDER BY sequence DESC LIMIT 1""", (rfq_id, revision)).fetchone()
            if decision == "ACCEPT":
                _, review = self._source(db, row["inbound_id"])
                if review["sequence"] != row["inbound_review_sequence"]:
                    raise ValueError("Inbound evidence changed; revise the RFQ")
                if json.loads(row["missing_json"]):
                    raise ValueError("RFQ requirements remain incomplete")
                payload = json.loads(row["payload_json"])
                product = db.execute("""SELECT p.id, p.sku, p.unit
                    FROM sell_opportunity o JOIN product p ON p.id = o.product_id
                    WHERE o.id = ?""",
                    (row["opportunity_id"],)).fetchone()
                if (product is None or payload["sku"] != product["sku"]
                        or payload["unit"] != product["unit"]):
                    raise ValueError("RFQ product, unit and corridor need resolution")
                profile = FindHandoff._approved_profile(db, product["id"])
                if payload["destination"] != json.loads(profile["payload_json"])["target_country"]:
                    raise ValueError("RFQ product, unit and corridor need resolution")
                if prior and prior["decision"] == "ACCEPT":
                    raise ValueError("RFQ revision already accepted")
            elif prior is None or prior["decision"] != "ACCEPT":
                raise ValueError("Only an effective RFQ acceptance can be revoked")
            cur = db.execute("""INSERT INTO sell_rfq_decision
                (rfq_id, revision, decision, payload_sha256, reason, actor_id, decided_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (rfq_id, revision, decision, row["payload_sha256"], reason, actor, _utc_now()))
            return cur.lastrowid

    def read(self, rfq_id: str) -> dict | None:
        self.store._require_access("READ_SELL_RFQ", rfq_id)
        with closing(self.store._connect()) as db:
            return self._read(db, rfq_id)

    def _read(self, db: object, rfq_id: str) -> dict | None:
        """Internal read in a caller-owned transaction after its access check."""
        row = db.execute("""SELECT q.*, r.revision, r.inbound_review_sequence,
            r.payload_json, r.payload_sha256, r.missing_json FROM sell_rfq q
            JOIN sell_rfq_revision r ON r.rfq_id = q.id
            WHERE q.id = ? ORDER BY r.revision DESC LIMIT 1""", (rfq_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        result["missing_fields"] = json.loads(result.pop("missing_json"))
        inbound = db.execute("SELECT * FROM sell_inbound_message WHERE id = ?",
                             (row["inbound_id"],)).fetchone()
        result["source"] = {key: inbound[key] for key in
                            ("source_system", "source_ref", "raw_ref", "received_at_utc",
                             "channel", "data_origin")}
        cited_review = db.execute("""SELECT evidence_ref, explanation FROM sell_inbound_review
            WHERE sequence = ?""", (row["inbound_review_sequence"],)).fetchone()
        result["source_review"] = dict(cited_review) if cited_review else None
        decision = db.execute("""SELECT decision FROM sell_rfq_decision
            WHERE rfq_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
            (rfq_id, row["revision"])).fetchone()
        review = db.execute("""SELECT sequence, classification, opportunity_id
            FROM sell_inbound_review WHERE inbound_id = ? ORDER BY sequence DESC LIMIT 1""",
            (row["inbound_id"],)).fetchone()
        current = (review is not None and review["sequence"] == row["inbound_review_sequence"]
                   and review["classification"] == "RFQ_CANDIDATE"
                   and review["opportunity_id"] == row["opportunity_id"])
        if current and decision and decision["decision"] == "ACCEPT":
            product = db.execute("""SELECT p.id, p.sku, p.unit
                FROM sell_opportunity o JOIN product p ON p.id = o.product_id
                WHERE o.id = ?""", (row["opportunity_id"],)).fetchone()
            try:
                profile = FindHandoff._approved_profile(db, product["id"])
                target_country = json.loads(profile["payload_json"])["target_country"]
                current = (result["payload"].get("sku") == product["sku"]
                           and result["payload"].get("unit") == product["unit"]
                           and result["payload"].get("destination") == target_country)
            except (ValueError, TypeError):
                current = False
        result["status"] = ("REVIEW_REQUIRED" if not current else
                            "ACCEPTED_SYNTHETIC" if decision and decision["decision"] == "ACCEPT"
                            else "REVOKED" if decision and decision["decision"] == "REVOKE"
                            else "DRAFT")
        result["quote_allowed"] = result["status"] == "ACCEPTED_SYNTHETIC"
        result["live_customer_request"] = False
        return result
