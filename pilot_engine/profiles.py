"""Versioned manufacturer product inputs for Find; no real data is bundled."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from typing import Any
from uuid import uuid4

from pilot_engine.store import PilotStore, _json, _utc_now


FIELDS = frozenset({
    "manufacturer_name", "manufacturer_country", "product_name", "sku", "unit",
    "drawing_ref", "dimensions", "material", "intended_use", "technical_limits",
    "claims", "search_terms", "target_country", "buyer_role", "hs6",
    "classification_status", "classification_note", "source_kind", "source_ref",
})
REQUIRED_FOR_APPROVAL = (
    "manufacturer_name", "manufacturer_country", "product_name", "sku", "unit",
    "material", "intended_use", "technical_limits", "target_country", "buyer_role",
    "hs6", "classification_note", "source_ref",
)


def _validate(payload: dict[str, Any], *, approval: bool = False) -> None:
    if not isinstance(payload, dict) or set(payload) - FIELDS:
        raise ValueError("Unknown or invalid product profile fields")
    for key in FIELDS - {"claims", "search_terms"}:
        value = payload.get(key)
        if value is not None and (not isinstance(value, str) or value != value.strip()):
            raise ValueError(f"Invalid {key}")
    for key in ("manufacturer_name", "product_name"):
        if not payload.get(key):
            raise ValueError(f"{key} is required even for a draft")
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
        if any(not payload.get(key) for key in REQUIRED_FOR_APPROVAL):
            raise ValueError("Manufacturer product profile is incomplete")
        if not (payload.get("drawing_ref") or payload.get("dimensions")):
            raise ValueError("Drawing reference or dimensions are required")
        if payload.get("classification_status") != "REVIEWED":
            raise ValueError("Classification needs manufacturer review")
        if payload.get("source_kind") != "MANUFACTURER":
            raise ValueError("Only manufacturer-sourced profiles can be approved")
        if any(not claim["confirmed_use"] for claim in payload.get("claims", [])):
            raise ValueError("Unconfirmed product claims cannot be approved")


class ProductProfiles:
    """Store immutable profile snapshots and decisions using the pilot access boundary."""

    def __init__(self, store: PilotStore):
        self.store = store

    def save_draft(
        self, payload: dict[str, Any], *, product_id: str | None = None,
        expected_revision: int = 0,
    ) -> tuple[str, int]:
        _validate(payload)
        if payload.get("hs6") not in (None, "", self.store.scope.hs6):
            raise ValueError("HS6 is outside the pilot research scope")
        if payload.get("target_country") not in (None, "", self.store.scope.country):
            raise ValueError("Target country is outside the pilot scope")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise ValueError("Expected revision must be a nonnegative integer")
        product_id = product_id or f"P-{uuid4()}"
        actor = self.store._require_access("EDIT_PROFILE", product_id)
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
            return {
                "product_id": product_id, "revision": row["revision"], "profile": payload,
                "payload_sha256": row["payload_sha256"], "created_by": row["actor_id"],
                "created_at_utc": row["created_at_utc"],
                "approved": event is not None and event["action"] == "APPROVED",
                "approved_search_terms": [item["text"] for item in payload.get("search_terms", [])
                                          if item["confirmed_use"] and event is not None
                                          and event["action"] == "APPROVED"],
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
                if payload["hs6"] != self.store.scope.hs6 or payload["target_country"] != self.store.scope.country:
                    raise ValueError("Profile is outside the pilot scope")
                db.execute("""UPDATE product SET sku = ?, name = ?, unit = ?, specification_ref = ?,
                    classification_status = 'REVIEWED', updated_at_utc = ? WHERE id = ?""",
                    (payload["sku"], payload["product_name"], payload["unit"],
                     payload.get("drawing_ref") or payload.get("dimensions"), _utc_now(), product_id))
                db.execute("""UPDATE manufacturer SET name = ? WHERE id =
                    (SELECT manufacturer_id FROM product WHERE id = ?)""",
                    (payload["manufacturer_name"], product_id))
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
            if event is None or event["action"] != "APPROVED":
                raise ValueError("Current product profile is not approved")
            db.execute("""UPDATE profile_revalidation SET reviewed_revision = ?, reviewed_by = ?,
                reviewed_at_utc = ? WHERE opportunity_id = ?""",
                (revision, actor, _utc_now(), opportunity_id))
            db.execute("""INSERT INTO audit_event
                (opportunity_id, action, actor_id, target_id, before_state, after_state,
                 source_ref, occurred_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (opportunity_id, "PRODUCT_PROFILE_REVIEWED", actor, row["product_id"],
                 None, str(revision), reason.strip(), _utc_now()))
