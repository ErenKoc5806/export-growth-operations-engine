"""Synthetic quotation revisions bound to reviewed RFQ and price evidence."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from pilot_engine.find_handoff import FindHandoff
from pilot_engine.manual_contact import _text, _time
from pilot_engine.rfq import SellRFQ
from pilot_engine.store import PilotStore, _json, _utc_now


def _price(value: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("Positive two-decimal price required")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Positive two-decimal price required") from exc
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        raise ValueError("Positive two-decimal price required")
    return amount


class Quotations:
    def __init__(self, store: PilotStore):
        self.store = store
        self.rfqs = SellRFQ(store)

    def register_price_authority(self, product_id: str, *, source_ref: str, content: bytes,
                                 sku: str, currency: str, unit_price_text: str,
                                 valid_until_utc: str) -> str:
        actor = self.store._require_access("EDIT_SELL_QUOTE", product_id)
        source_ref = _text(source_ref, "pricing source", 300)
        sku = _text(sku, "SKU", 300)
        if not isinstance(content, bytes) or not 0 < len(content) <= 20_000_000:
            raise ValueError("Price source document must contain 1 to 20 MB")
        price = _price(unit_price_text)
        valid_until = _time(valid_until_utc)
        if datetime.fromisoformat(valid_until) <= datetime.now(timezone.utc):
            raise ValueError("Pricing validity must be in the future")
        if currency != self.store.scope.currency:
            raise ValueError("Currency is outside pilot scope")
        digest = hashlib.sha256(content).hexdigest()
        with self.store._transaction() as db:
            profile = FindHandoff._approved_profile(db, product_id)
            if json.loads(profile["payload_json"])["sku"] != sku:
                raise ValueError("Pricing SKU differs from approved product")
            prior = db.execute("SELECT * FROM sell_price_authority WHERE source_ref = ?",
                               (source_ref,)).fetchone()
            if prior:
                if (prior["product_id"], prior["source_sha256"], prior["sku"],
                        prior["currency"], prior["unit_price_text"], prior["valid_until_utc"]) != (
                        product_id, digest, sku, currency, str(price), valid_until):
                    raise ValueError("Pricing source reference is already bound to other terms")
                return prior["id"]
            price_id = f"PA-{uuid4()}"
            db.execute("""INSERT INTO sell_price_authority
                (id, product_id, source_ref, source_sha256, byte_length, sku, currency,
                 unit_price_text, valid_until_utc, data_origin, actor_id, registered_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SYNTHETIC', ?, ?)""",
                (price_id, product_id, source_ref, digest, len(content), sku, currency,
                 str(price), valid_until, actor, _utc_now()))
            return price_id

    def save(self, rfq_id: str, price_authority_id: str, *, valid_until_utc: str,
             exclusions: str, quotation_id: str | None = None,
             expected_revision: int = 0) -> tuple[str, int]:
        actor = self.store._require_access("EDIT_SELL_QUOTE", rfq_id)
        validity = _time(valid_until_utc)
        if datetime.fromisoformat(validity) <= datetime.now(timezone.utc):
            raise ValueError("Quotation validity must be in the future")
        if not isinstance(exclusions, str) or len(exclusions) > 2000:
            raise ValueError("Exclusions must be a reviewable string")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Expected quotation revision must be nonnegative")
        with self.store._transaction() as db:
            rfq = self.rfqs._read(db, rfq_id)
            if rfq is None or rfq["status"] != "ACCEPTED_SYNTHETIC":
                raise ValueError("Current accepted synthetic RFQ is required")
            authority = db.execute("SELECT * FROM sell_price_authority WHERE id = ?",
                                   (price_authority_id,)).fetchone()
            product = db.execute("""SELECT p.id FROM sell_rfq q
                JOIN sell_opportunity o ON o.id = q.opportunity_id
                JOIN product p ON p.id = o.product_id WHERE q.id = ?""", (rfq_id,)).fetchone()
            if (authority is None or product is None or authority["product_id"] != product["id"]
                    or authority["sku"] != rfq["payload"]["sku"]
                    or datetime.fromisoformat(validity) >
                    datetime.fromisoformat(authority["valid_until_utc"])):
                raise ValueError("Current product-bound price authority is required")
            profile = FindHandoff._approved_profile(db, product["id"])
            profile_payload = json.loads(profile["payload_json"])
            quantity = Decimal(rfq["payload"]["quantity"])
            if rfq["payload"]["unit"] == "PCS" and quantity != quantity.to_integral_value():
                raise ValueError("Piece quantity must be whole")
            price = _price(authority["unit_price_text"])
            total = quantity * price
            if total.as_tuple().exponent < -2:
                raise ValueError("Quotation total has unsupported currency precision")
            payload = {"rfq_id": rfq_id, "rfq_revision": rfq["revision"],
                       "sku": rfq["payload"]["sku"],
                       "specification": rfq["payload"]["specification"],
                       "quantity": str(quantity), "unit": rfq["payload"]["unit"],
                       "currency": authority["currency"], "unit_price": str(price),
                       "total": str(total), "incoterm_code": profile_payload["incoterm_code"],
                       "incoterm_place": profile_payload["incoterm_place"],
                       "payment_terms": profile_payload["payment_terms"],
                       "lead_time_days": profile_payload["lead_time_days"],
                       "product_profile_revision": profile["revision"],
                       "product_profile_sha256": profile["payload_sha256"],
                       "valid_until_utc": validity, "exclusions": exclusions,
                       "price_source_ref": authority["source_ref"],
                       "price_source_sha256": authority["source_sha256"]}
            digest = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
            existing = db.execute("SELECT id FROM sell_quotation WHERE id = ?",
                                  (quotation_id,)).fetchone() if quotation_id else None
            if quotation_id is None:
                if expected_revision != 0:
                    raise ValueError("Quotation does not exist")
                quotation_id = f"QT-{uuid4()}"
                db.execute("""INSERT INTO sell_quotation
                    (id, rfq_id, actor_id, created_at_utc) VALUES (?, ?, ?, ?)""",
                    (quotation_id, rfq_id, actor, _utc_now()))
                revision = 1
            else:
                if existing is None:
                    raise ValueError("Quotation does not exist")
                prior = db.execute("""SELECT q.rfq_id, r.revision FROM sell_quotation q
                    JOIN sell_quotation_revision r ON r.quotation_id = q.id
                    WHERE q.id = ? ORDER BY r.revision DESC LIMIT 1""",
                    (quotation_id,)).fetchone()
                if prior["rfq_id"] != rfq_id or prior["revision"] != expected_revision:
                    raise ValueError("Quotation revision changed; reload before editing")
                revision = expected_revision + 1
            db.execute("""INSERT INTO sell_quotation_revision
                (quotation_id, revision, rfq_id, rfq_revision, price_authority_id,
                 payload_json, payload_sha256, actor_id, created_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (quotation_id, revision, rfq_id, rfq["revision"], price_authority_id,
                 _json(payload), digest, actor, _utc_now()))
            return quotation_id, revision

    def decide(self, quotation_id: str, revision: int, decision: str, *,
               expected_payload_sha256: str | None, reason: str) -> int:
        actor = self.store._require_access("APPROVE_SELL_QUOTE", quotation_id)
        reason = _text(reason, "quotation decision reason", 2000)
        if decision not in ("APPROVE", "REVOKE"):
            raise ValueError("Quotation decision must approve or revoke")
        with self.store._transaction() as db:
            quote = self._read(db, quotation_id)
            if quote is None or quote["revision"] != revision:
                raise ValueError("Current quotation revision is required")
            prior = db.execute("""SELECT decision FROM sell_quotation_decision
                WHERE quotation_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
                (quotation_id, revision)).fetchone()
            if decision == "APPROVE":
                if (quote["status"] not in ("DRAFT", "REVOKED")
                        or quote["payload_sha256"] != expected_payload_sha256):
                    raise ValueError("Current exact quotation payload approval required")
                if prior and prior["decision"] == "APPROVE":
                    raise ValueError("This quotation revision is already approved")
            elif prior is None or prior["decision"] != "APPROVE":
                raise ValueError("Only an effective approval can be revoked")
            cur = db.execute("""INSERT INTO sell_quotation_decision
                (quotation_id, revision, decision, payload_sha256, reason, actor_id, decided_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (quotation_id, revision, decision, quote["payload_sha256"], reason,
                 actor, _utc_now()))
            return cur.lastrowid

    def _read(self, db: object, quotation_id: str) -> dict | None:
        row = db.execute("""SELECT q.rfq_id, r.* FROM sell_quotation q
            JOIN sell_quotation_revision r ON r.quotation_id = q.id
            WHERE q.id = ? ORDER BY r.revision DESC LIMIT 1""", (quotation_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        rfq = self.rfqs._read(db, row["rfq_id"])
        authority = db.execute("SELECT * FROM sell_price_authority WHERE id = ?",
                               (row["price_authority_id"],)).fetchone()
        current = (rfq is not None and rfq["status"] == "ACCEPTED_SYNTHETIC"
                   and rfq["revision"] == row["rfq_revision"]
                   and authority is not None and authority["source_sha256"] ==
                   result["payload"]["price_source_sha256"])
        if current:
            try:
                profile = FindHandoff._approved_profile(db, authority["product_id"])
                current = (profile["revision"] == result["payload"]["product_profile_revision"]
                           and profile["payload_sha256"] ==
                           result["payload"]["product_profile_sha256"])
            except ValueError:
                current = False
        expired = (datetime.fromisoformat(result["payload"]["valid_until_utc"])
                   <= datetime.now(timezone.utc) or authority is None or
                   datetime.fromisoformat(authority["valid_until_utc"]) <= datetime.now(timezone.utc))
        decision = db.execute("""SELECT decision FROM sell_quotation_decision
            WHERE quotation_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
            (quotation_id, row["revision"])).fetchone()
        result["status"] = ("REVIEW_REQUIRED" if not current else
                            "EXPIRED" if expired else
                            "APPROVED_SYNTHETIC" if decision and decision["decision"] == "APPROVE"
                            else "REVOKED" if decision and decision["decision"] == "REVOKE"
                            else "DRAFT")
        result["send_allowed"] = False
        result["customer_accepted"] = False
        result["live_commercial_offer"] = False
        return result

    def read(self, quotation_id: str) -> dict | None:
        self.store._require_access("READ_SELL_QUOTE", quotation_id)
        with closing(self.store._connect()) as db:
            return self._read(db, quotation_id)
