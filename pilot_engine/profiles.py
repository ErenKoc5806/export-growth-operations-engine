"""Versioned manufacturer product inputs for Find; no real data is bundled."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from contextlib import closing
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from pilot_engine.store import PilotStore, _json, _utc_now


FIELDS = frozenset({
    "manufacturer_name", "manufacturer_country", "product_name", "sku", "unit",
    "drawing_ref", "dimensions", "material", "intended_use", "technical_limits",
    "claims", "search_terms", "target_country", "buyer_role", "hs6",
    "classification_status", "classification_note", "source_kind", "source_ref",
    "source_sha256", "minimum_order_quantity", "lead_time_days", "payment_terms",
    "incoterm_code", "incoterm_place", "manufacturer_legal_id",
})
REQUIRED_FOR_APPROVAL = (
    "manufacturer_name", "manufacturer_country", "product_name", "sku", "unit",
    "material", "intended_use", "technical_limits", "target_country", "buyer_role",
    "hs6", "classification_note", "source_ref", "source_sha256",
    "minimum_order_quantity", "lead_time_days", "payment_terms",
    "incoterm_code", "incoterm_place",
)
INCOTERMS = frozenset({"EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP", "FAS", "FOB", "CFR", "CIF"})


def _name_key(name: str) -> str:
    """Use a comparison hint, not a legal identity (I/İ/ı/i compare equally)."""
    return unicodedata.normalize("NFKC", name).casefold().replace("ı", "i").replace("i\u0307", "i")


def _validate(payload: dict[str, Any], *, approval: bool = False) -> None:
    if not isinstance(payload, dict) or set(payload) - FIELDS:
        raise ValueError("Unknown or invalid product profile fields")
    for key in FIELDS - {"claims", "search_terms", "lead_time_days"}:
        value = payload.get(key)
        if value is not None and (not isinstance(value, str) or value != value.strip()):
            raise ValueError(f"Invalid {key}")
    for key in ("manufacturer_name", "product_name"):
        if not payload.get(key):
            raise ValueError(f"{key} is required even for a draft")
    if payload.get("manufacturer_legal_id") and not payload.get("manufacturer_country"):
        raise ValueError("Manufacturer legal ID needs a country")
    if payload.get("lead_time_days") is not None and (type(payload["lead_time_days"]) is not int
            or payload["lead_time_days"] <= 0):
        raise ValueError("lead_time_days must be a positive integer")
    if payload.get("minimum_order_quantity") not in (None, ""):
        try:
            amount = Decimal(payload["minimum_order_quantity"])
        except (InvalidOperation, TypeError) as exc:
            raise ValueError("Invalid minimum_order_quantity") from exc
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Invalid minimum_order_quantity")
    if payload.get("source_sha256") not in (None, ""):
        digest = payload["source_sha256"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("source_sha256 must be a lowercase SHA-256 hex digest")
    if payload.get("incoterm_code") not in (None, "") and payload["incoterm_code"] not in INCOTERMS:
        raise ValueError("Invalid Incoterms code")
    for key in ("claims", "search_terms"):
        values = payload.get(key, [])
        if not isinstance(values, list):
            raise ValueError(f"{key} must be a list")
        for item in values:
            if not isinstance(item, dict) or set(item) != {"text", "confirmed_use", "evidence_ref"}:
                raise ValueError(f"{key} needs text, confirmed_use and evidence_ref")
            if (not isinstance(item["text"], str) or not item["text"].strip()
                    or item["text"] != item["text"].strip()
                    or not isinstance(item["confirmed_use"], bool)
                    or not isinstance(item["evidence_ref"], str)):
                raise ValueError(f"Invalid {key} entry")
            if item["confirmed_use"] and not item["evidence_ref"].strip():
                raise ValueError("Confirmed use needs technical evidence")
    if approval:
        if any(payload.get(key) in (None, "", 0) for key in REQUIRED_FOR_APPROVAL):
            raise ValueError("Manufacturer product profile is incomplete")
        if not (payload.get("drawing_ref") or payload.get("dimensions")):
            raise ValueError("Drawing reference or dimensions are required")
        if payload.get("classification_status") != "REVIEWED":
            raise ValueError("Classification needs manufacturer review")
        if payload.get("source_kind") != "MANUFACTURER":
            raise ValueError("Only manufacturer-sourced profiles can be approved")
        if any(not claim["confirmed_use"] for claim in payload.get("claims", [])):
            raise ValueError("Unconfirmed product claims cannot be approved")


def _source_verified(db: Any, payload: dict[str, Any]) -> bool:
    if not payload.get("source_ref") or not payload.get("source_sha256"):
        return False
    row = db.execute("SELECT sha256 FROM profile_source_document WHERE source_ref = ?",
                     (payload["source_ref"],)).fetchone()
    return row is not None and row["sha256"] == payload["source_sha256"]


def _approval_valid(db: Any, payload: dict[str, Any]) -> bool:
    try:
        _validate(payload, approval=True)
    except ValueError:
        return False
    return _source_verified(db, payload)


class ProductProfiles:
    """Store immutable profile snapshots and decisions using the pilot access boundary."""

    def __init__(self, store: PilotStore):
        self.store = store

    def register_source_document(self, source_ref: str, content: bytes) -> str:
        """Register a digest of the supplied original; retain the original externally."""
        actor = self.store._require_access("EDIT_PROFILE", source_ref)
        if not isinstance(source_ref, str) or not source_ref.strip() or source_ref != source_ref.strip():
            raise ValueError("Source reference is required")
        if not isinstance(content, bytes) or not 0 < len(content) <= 20_000_000:
            raise ValueError("Source document must contain 1 to 20 MB of bytes")
        digest = hashlib.sha256(content).hexdigest()
        with self.store._transaction() as db:
            prior = db.execute("SELECT sha256 FROM profile_source_document WHERE source_ref = ?",
                               (source_ref,)).fetchone()
            if prior is not None:
                if prior["sha256"] != digest:
                    raise ValueError("Source reference is already bound to another document")
                return digest
            db.execute("""INSERT INTO profile_source_document
                (source_ref, sha256, byte_length, actor_id, registered_at_utc)
                VALUES (?, ?, ?, ?, ?)""", (source_ref, digest, len(content), actor, _utc_now()))
        return digest

    def save_draft(
        self, payload: dict[str, Any], *, product_id: str | None = None,
        expected_revision: int = 0, manufacturer_id: str | None = None,
    ) -> tuple[str, int]:
        product_id = product_id or f"P-{uuid4()}"
        actor = self.store._require_access("EDIT_PROFILE", product_id)
        _validate(payload)
        if payload.get("hs6") not in (None, "", self.store.scope.hs6):
            raise ValueError("HS6 is outside the pilot research scope")
        if payload.get("target_country") not in (None, "", self.store.scope.country):
            raise ValueError("Target country is outside the pilot scope")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise ValueError("Expected revision must be a nonnegative integer")
        data = _json(payload)
        digest = hashlib.sha256(data.encode("utf-8")).hexdigest()
        now = _utc_now()
        with self.store._transaction() as db:
            current = db.execute("SELECT MAX(revision) FROM product_profile_revision WHERE product_id = ?",
                                 (product_id,)).fetchone()[0] or 0
            if current != expected_revision:
                raise ValueError("Product profile revision changed; reload before editing")
            if current == 0:
                if db.execute("SELECT 1 FROM product WHERE id = ?", (product_id,)).fetchone():
                    raise ValueError("Product already exists without a profile")
                existing = db.execute("""SELECT DISTINCT m.id, r.payload_json FROM manufacturer m
                    JOIN product p ON p.manufacturer_id = m.id
                    JOIN product_profile_revision r ON r.product_id = p.id AND r.revision =
                        (SELECT MAX(revision) FROM product_profile_revision WHERE product_id = p.id)
                    ORDER BY m.created_at_utc, m.id""").fetchall()
                identities: dict[str, list[dict[str, Any]]] = {}
                for entry in existing:
                    identities.setdefault(entry["id"], []).append(json.loads(entry["payload_json"]))
                country = payload.get("manufacturer_country")
                legal_id = payload.get("manufacturer_legal_id")
                legal_owners = [mid for mid, records in identities.items() if any(
                    saved.get("manufacturer_country") == country
                    and saved.get("manufacturer_legal_id") == legal_id for saved in records)] if legal_id else []
                if manufacturer_id is not None:
                    if manufacturer_id not in identities:
                        raise ValueError("Selected manufacturer has no existing product profile")
                    records = identities[manufacturer_id]
                    if (not any(saved.get("manufacturer_country") in (None, "", country)
                                and _name_key(saved["manufacturer_name"]) ==
                                _name_key(payload["manufacturer_name"]) for saved in records)
                            or any(saved.get("manufacturer_legal_id") not in (None, "", legal_id)
                                   for saved in records)):
                        raise ValueError("Selected manufacturer identity conflicts with the profile")
                    if any(mid != manufacturer_id for mid in legal_owners):
                        raise ValueError("Legal ID belongs to a different manufacturer")
                elif legal_id:
                    if len(legal_owners) > 1:
                        raise ValueError("Legal ID belongs to multiple manufacturer rows; review required")
                    manufacturer_id = legal_owners[0] if legal_owners else None
                    if manufacturer_id is not None and any(
                            saved.get("manufacturer_legal_id") not in (None, "", legal_id)
                            for saved in identities[manufacturer_id]):
                        raise ValueError("Manufacturer row has conflicting legal IDs; review required")
                else:
                    name_matches = [mid for mid, records in identities.items() if any(
                        saved.get("manufacturer_country") in (None, "", country)
                        and _name_key(saved["manufacturer_name"]) ==
                        _name_key(payload["manufacturer_name"]) for saved in records)]
                    if name_matches:
                        raise ValueError("Name-only manufacturer match needs an explicit manufacturer_id or legal ID")
                if manufacturer_id is None:
                    manufacturer_id = f"M-{uuid4()}"
                    db.execute("INSERT INTO manufacturer (id, name, created_at_utc) VALUES (?, ?, ?)",
                               (manufacturer_id, payload["manufacturer_name"], now))
                db.execute("""INSERT INTO product
                    (id, manufacturer_id, sku, name, unit, hs6, specification_ref,
                     classification_status, created_at_utc, updated_at_utc)
                    VALUES (?, ?, NULL, ?, ?, ?, NULL, 'UNVERIFIED', ?, ?)""",
                    (product_id, manufacturer_id, payload["product_name"],
                     payload.get("unit") or "UNSPECIFIED", self.store.scope.hs6, now, now))
            else:
                if manufacturer_id is not None:
                    raise ValueError("An existing product cannot change manufacturer_id")
                previous = db.execute("""SELECT payload_json FROM product_profile_revision
                    WHERE product_id = ? AND revision = ?""", (product_id, current)).fetchone()
                old = json.loads(previous["payload_json"])
                if (_name_key(old["manufacturer_name"]) != _name_key(payload["manufacturer_name"])
                        or (old.get("manufacturer_country") and old["manufacturer_country"] !=
                            payload.get("manufacturer_country"))
                        or (old.get("manufacturer_legal_id") and old["manufacturer_legal_id"] !=
                            payload.get("manufacturer_legal_id"))):
                    raise ValueError("Manufacturer identity cannot change on an existing product")
                shared_id = db.execute("SELECT manufacturer_id FROM product WHERE id = ?",
                                       (product_id,)).fetchone()["manufacturer_id"]
                for other in db.execute("""SELECT r.payload_json FROM product p
                    JOIN product_profile_revision r ON r.product_id = p.id AND r.revision =
                        (SELECT MAX(revision) FROM product_profile_revision WHERE product_id = p.id)
                    WHERE p.manufacturer_id = ? AND p.id != ?""", (shared_id, product_id)):
                    saved = json.loads(other["payload_json"])
                    if (saved.get("manufacturer_legal_id") and payload.get("manufacturer_legal_id")
                            and saved["manufacturer_legal_id"] != payload["manufacturer_legal_id"]):
                        raise ValueError("Manufacturer legal ID conflicts with another product")
                if payload.get("manufacturer_legal_id"):
                    for other in db.execute("""SELECT p.manufacturer_id, r.payload_json FROM product p
                        JOIN product_profile_revision r ON r.product_id = p.id AND r.revision =
                            (SELECT MAX(revision) FROM product_profile_revision WHERE product_id = p.id)
                        WHERE p.manufacturer_id != ?""", (shared_id,)):
                        saved = json.loads(other["payload_json"])
                        if (saved.get("manufacturer_country") == payload.get("manufacturer_country")
                                and saved.get("manufacturer_legal_id") == payload["manufacturer_legal_id"]):
                            raise ValueError("Legal ID belongs to a different manufacturer")
                db.execute("""UPDATE product SET classification_status = 'REVIEW_REQUIRED',
                    updated_at_utc = ? WHERE id = ?""", (now, product_id))
            revision = current + 1
            db.execute("""INSERT INTO product_profile_revision
                (product_id, revision, payload_json, payload_sha256, actor_id, created_at_utc)
                VALUES (?, ?, ?, ?, ?, ?)""", (product_id, revision, data, digest, actor, now))
            # Every changed snapshot needs a fresh buyer-fit and outreach review.
            for row in db.execute("SELECT id FROM opportunity WHERE product_id = ?", (product_id,)):
                db.execute("""INSERT INTO profile_revalidation
                    (opportunity_id, required_revision, reviewed_revision)
                    VALUES (?, ?, 0) ON CONFLICT(opportunity_id) DO UPDATE SET
                    required_revision = excluded.required_revision""", (row["id"], revision))
        return product_id, revision

    def read(self, product_id: str) -> dict[str, Any] | None:
        self.store._require_access("READ_PROFILE", product_id)
        with closing(self.store._connect()) as db:
            row = db.execute("""SELECT revision, payload_json, payload_sha256, actor_id,
                created_at_utc FROM product_profile_revision WHERE product_id = ?
                ORDER BY revision DESC LIMIT 1""", (product_id,)).fetchone()
            if row is None:
                return None
            event = db.execute("""SELECT action FROM product_profile_event
                WHERE product_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
                (product_id, row["revision"])).fetchone()
            payload = json.loads(row["payload_json"])
            approved = (event is not None and event["action"] == "APPROVED"
                        and _approval_valid(db, payload))
            return {
                "product_id": product_id, "revision": row["revision"], "profile": payload,
                "manufacturer_id": db.execute("SELECT manufacturer_id FROM product WHERE id = ?",
                                              (product_id,)).fetchone()["manufacturer_id"],
                "payload_sha256": row["payload_sha256"], "created_by": row["actor_id"],
                "created_at_utc": row["created_at_utc"],
                "approved": approved,
                "approved_search_terms": [item["text"] for item in payload.get("search_terms", [])
                                          if item["confirmed_use"] and approved],
            }

    def decide(self, product_id: str, revision: int, action: str, reason: str) -> None:
        if action not in ("APPROVED", "REVOKED") or not isinstance(reason, str) or not reason.strip():
            raise ValueError("Profile decision needs an action and reason")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("Profile decision needs a positive revision")
        actor = self.store._require_access("APPROVE_PROFILE", product_id)
        with self.store._transaction() as db:
            row = db.execute("""SELECT revision, payload_json FROM product_profile_revision
                WHERE product_id = ? ORDER BY revision DESC LIMIT 1""", (product_id,)).fetchone()
            if row is None or row["revision"] != revision:
                raise ValueError("Only the current product profile revision can be decided")
            prior = db.execute("""SELECT action FROM product_profile_event
                WHERE product_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
                (product_id, revision)).fetchone()
            if prior is not None and prior["action"] == action:
                raise ValueError("Profile already has that effective decision")
            payload = json.loads(row["payload_json"])
            if action == "APPROVED":
                _validate(payload, approval=True)
                if not _source_verified(db, payload):
                    raise ValueError("Profile source document hash does not match a registered document")
                if payload["hs6"] != self.store.scope.hs6 or payload["target_country"] != self.store.scope.country:
                    raise ValueError("Profile is outside the pilot scope")
                db.execute("""UPDATE product SET sku = ?, name = ?, unit = ?, specification_ref = ?,
                    classification_status = 'REVIEWED', updated_at_utc = ? WHERE id = ?""",
                    (payload["sku"], payload["product_name"], payload["unit"],
                     payload.get("drawing_ref") or payload.get("dimensions"), _utc_now(), product_id))
            db.execute("""INSERT INTO product_profile_event
                (product_id, revision, action, actor_id, reason, occurred_at_utc)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (product_id, revision, action, actor, reason.strip(), _utc_now()))
            if action == "REVOKED":
                db.execute("""UPDATE product SET classification_status = 'REVIEW_REQUIRED',
                    updated_at_utc = ? WHERE id = ?""", (_utc_now(), product_id))
                for opportunity in db.execute("SELECT id FROM opportunity WHERE product_id = ?", (product_id,)):
                    db.execute("""INSERT INTO profile_revalidation
                        (opportunity_id, required_revision, reviewed_revision)
                        VALUES (?, ?, 0) ON CONFLICT(opportunity_id) DO UPDATE SET
                        required_revision = excluded.required_revision, reviewed_revision = 0""",
                        (opportunity["id"], revision))

    def require_current_approval(self, product_id: str, revision: int) -> dict[str, Any]:
        profile = self.read(product_id)
        if profile is None or profile["revision"] != revision or not profile["approved"]:
            raise ValueError("Current manufacturer product profile approval is required")
        return profile

    def acknowledge_revalidation(self, opportunity_id: str, revision: int, reason: str) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Buyer fit and outreach review needs a reason")
        actor = self.store._require_access("APPROVE_PROFILE", opportunity_id)
        with self.store._transaction() as db:
            row = db.execute("""SELECT o.product_id, r.required_revision FROM opportunity o
                JOIN profile_revalidation r ON r.opportunity_id = o.id WHERE o.id = ?""",
                (opportunity_id,)).fetchone()
            if row is None or row["required_revision"] != revision:
                raise ValueError("No matching profile change to review")
            event = db.execute("""SELECT action FROM product_profile_event
                WHERE product_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
                (row["product_id"], revision)).fetchone()
            latest = db.execute("""SELECT payload_json FROM product_profile_revision
                WHERE product_id = ? ORDER BY revision DESC LIMIT 1""", (row["product_id"],)).fetchone()
            if (event is None or event["action"] != "APPROVED" or latest is None
                    or not _approval_valid(db, json.loads(latest["payload_json"]))):
                raise ValueError("Current product profile is not approved")
            db.execute("""UPDATE profile_revalidation SET reviewed_revision = ?, reviewed_by = ?,
                reviewed_at_utc = ? WHERE opportunity_id = ?""",
                (revision, actor, _utc_now(), opportunity_id))
            db.execute("""INSERT INTO audit_event
                (opportunity_id, action, actor_id, target_id, before_state, after_state,
                 source_ref, occurred_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (opportunity_id, "PRODUCT_PROFILE_REVIEWED", actor, row["product_id"],
                 None, str(revision), reason.strip(), _utc_now()))
