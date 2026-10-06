"""Reviewable, versioned outreach text from synthetic Find research. No sending."""

from __future__ import annotations

import json
from contextlib import closing
from uuid import uuid4

from pilot_engine.find_handoff import FindHandoff
from pilot_engine.store import PilotStore, _json, _utc_now


CHANNELS = {"GENERIC_EMAIL": "EMAIL", "NAMED_EMAIL": "EMAIL",
            "CONTACT_FORM": "CONTACT_FORM", "SWITCHBOARD": "PHONE_SCRIPT",
            "NAMED_PHONE": "PHONE_SCRIPT"}


def _required(value: str, field: str, limit: int) -> str:
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(value) > limit or any(ord(c) < 32 and (field != "body" or c not in "\n\t")
                                       for c in value)):
        raise ValueError(f"Invalid {field}")
    return value


class SellDrafts:
    def __init__(self, store: PilotStore):
        self.store = store
        self.handoffs = FindHandoff(store)

    def _context(self, db: object, handoff_id: str) -> tuple[dict, dict, dict]:
        handoff = self.handoffs._read(db, handoff_id)
        if handoff is None or handoff["status"] != "CURRENT_RESEARCH":
            raise ValueError("Current Find handoff is required")
        if handoff["data_origin"] != "SYNTHETIC" or not handoff["route_value"]:
            raise ValueError("Only synthetic, visible routes can be drafted")
        route = db.execute("SELECT kind FROM discovered_contact_route WHERE id = ?",
                           (handoff["route_id"],)).fetchone()
        profile = db.execute("""SELECT payload_json FROM product_profile_revision
            WHERE product_id = ? AND revision = ? AND payload_sha256 = ?""",
            (handoff["product_id"], handoff["profile_revision"],
             handoff["profile_sha256"])).fetchone()
        if route is None or profile is None:
            raise ValueError("Handoff evidence is missing")
        return handoff, dict(route), json.loads(profile["payload_json"])

    @staticmethod
    def _claims(profile: dict, claim_refs: list[str]) -> list[str]:
        if not isinstance(claim_refs, list) or len(set(claim_refs)) != len(claim_refs):
            raise ValueError("Claim references must be a unique list")
        approved: dict[str, list[str]] = {}
        for item in profile.get("claims", []):
            if item["confirmed_use"] and item["evidence_ref"]:
                approved.setdefault(item["evidence_ref"], []).append(item["text"])
        if any(not isinstance(ref, str) or ref not in approved for ref in claim_refs):
            raise ValueError("Only supported product claims may be selected")
        return [claim for ref in claim_refs for claim in approved[ref]]

    @staticmethod
    def _warnings(profile: dict, subject: str, body: str, source: str) -> list[str]:
        combined = (subject + "\n" + body).casefold()
        warnings = ["HUMAN_CLAIM_AND_RECIPIENT_REVIEW_REQUIRED"]
        if source == "OPERATOR_EDIT":
            warnings.append("EDITED_TEXT_CLAIMS_UNVERIFIED")
        for term in profile.get("claims", []) + profile.get("search_terms", []):
            if not term["confirmed_use"] and term["text"].casefold() in combined:
                warnings.append("UNCONFIRMED_APPLICATION: " + term["text"])
        return warnings

    def create(self, handoff_id: str, *, sender_name: str, sender_address: str,
               language: str = "en", claim_refs: list[str] | None = None) -> tuple[str, int]:
        actor = self.store._require_access("EDIT_SELL_DRAFT", handoff_id)
        sender_name = _required(sender_name, "sender name", 160)
        sender_address = _required(sender_address, "sender address", 320)
        if language not in ("en", "de", "tr"):
            raise ValueError("Unsupported draft language")
        if claim_refs is None:
            claim_refs = []
        with self.store._transaction() as db:
            handoff, route, profile = self._context(db, handoff_id)
            claims = self._claims(profile, claim_refs)
            channel = CHANNELS[route["kind"]]
            if channel == "EMAIL" and ("@" not in sender_address or " " in sender_address):
                raise ValueError("Email draft needs a sender email address")
            if language == "de":
                subject = f"Anfrage zu {profile['product_name']}"
                body = (f"Guten Tag,\n\nwir möchten mit {handoff['buyer_company_name']} "
                        f"über {profile['product_name']} (SKU {profile['sku']}) sprechen.")
                ending = "Bitte teilen Sie uns mit, ob ein Austausch sinnvoll wäre.\n\nMit freundlichen Grüßen"
            elif language == "tr":
                subject = f"{profile['product_name']} hakkında görüşme"
                body = (f"Merhaba,\n\n{handoff['buyer_company_name']} ile "
                        f"{profile['product_name']} (SKU {profile['sku']}) hakkında görüşmek isteriz.")
                ending = "Görüşmenin uygun olup olmadığını bildirebilir misiniz?\n\nSaygılarımla"
            else:
                subject = f"Inquiry about {profile['product_name']}"
                body = (f"Hello,\n\nWe would like to speak with {handoff['buyer_company_name']} "
                        f"about {profile['product_name']} (SKU {profile['sku']}).")
                ending = "Please let us know if a conversation would be useful.\n\nBest regards"
            if claims:
                body += "\n\n" + "\n".join(claims)
            body += "\n\n" + ending + "\n" + sender_name
            draft_id = f"SD-{uuid4()}"
            db.execute("INSERT INTO sell_draft (id, handoff_id, created_at_utc) VALUES (?, ?, ?)",
                       (draft_id, handoff_id, _utc_now()))
            self._insert(db, draft_id, 1, handoff, channel, sender_name, sender_address,
                         language, subject, body, claim_refs, "TEMPLATE", actor, profile)
        return draft_id, 1

    def revise(self, draft_id: str, expected_revision: int, *, sender_name: str,
               sender_address: str, language: str, subject: str, body: str,
               claim_refs: list[str], handoff_id: str | None = None) -> int:
        actor = self.store._require_access("EDIT_SELL_DRAFT", draft_id)
        sender_name = _required(sender_name, "sender name", 160)
        sender_address = _required(sender_address, "sender address", 320)
        subject = _required(subject, "subject", 300)
        body = _required(body, "body", 10000)
        if language not in ("en", "de", "tr"):
            raise ValueError("Unsupported draft language")
        with self.store._transaction() as db:
            prior = db.execute("""SELECT d.handoff_id, r.revision FROM sell_draft d
                JOIN sell_draft_revision r ON r.draft_id = d.id WHERE d.id = ?
                ORDER BY r.revision DESC LIMIT 1""", (draft_id,)).fetchone()
            if prior is None or prior["revision"] != expected_revision:
                raise ValueError("Draft revision changed; reload before editing")
            handoff, route, profile = self._context(db, handoff_id or prior["handoff_id"])
            if handoff_id and handoff_id != prior["handoff_id"]:
                original = db.execute("""SELECT product_id, candidate_id FROM find_handoff
                    WHERE id = ?""", (prior["handoff_id"],)).fetchone()
                if (handoff["product_id"], handoff["candidate_id"]) != tuple(original):
                    raise ValueError("Recipient change must stay with the reviewed product and company")
            self._claims(profile, claim_refs)
            channel = CHANNELS[route["kind"]]
            if channel == "EMAIL" and ("@" not in sender_address or " " in sender_address):
                raise ValueError("Email draft needs a sender email address")
            revision = expected_revision + 1
            self._insert(db, draft_id, revision, handoff, channel, sender_name, sender_address,
                         language, subject, body, claim_refs, "OPERATOR_EDIT", actor, profile)
        return revision

    @classmethod
    def _insert(cls, db: object, draft_id: str, revision: int, handoff: dict,
                channel: str, sender_name: str, sender_address: str, language: str,
                subject: str, body: str, claim_refs: list[str], source: str,
                actor: str, profile: dict) -> None:
        db.execute("""INSERT INTO sell_draft_revision
            (draft_id, revision, handoff_id, handoff_revision, fit_sequence, profile_sha256,
             recipient_route_id, recipient_value, channel, sender_name, sender_address,
             language, subject, body, claim_refs_json, warnings_json, source, actor_id, created_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (draft_id, revision, handoff["id"], handoff["revision"],
             handoff["fit_sequence"], handoff["profile_sha256"],
             handoff["route_id"], handoff["route_value"], channel, sender_name,
             sender_address, language, subject, body, _json(claim_refs),
             _json(cls._warnings(profile, subject, body, source)), source, actor, _utc_now()))

    def reject(self, draft_id: str, revision: int, reason: str) -> None:
        actor = self.store._require_access("EDIT_SELL_DRAFT", draft_id)
        reason = _required(reason, "rejection reason", 1000)
        with self.store._transaction() as db:
            latest = db.execute("""SELECT MAX(revision) FROM sell_draft_revision
                WHERE draft_id = ?""", (draft_id,)).fetchone()[0]
            if latest != revision:
                raise ValueError("Only the latest draft revision can be rejected")
            db.execute("""INSERT INTO sell_draft_rejection
                (draft_id, revision, reason, actor_id, rejected_at_utc) VALUES (?, ?, ?, ?, ?)""",
                (draft_id, revision, reason, actor, _utc_now()))

    def read(self, draft_id: str, revision: int | None = None) -> dict | None:
        self.store._require_access("READ_SELL_DRAFT", draft_id)
        with closing(self.store._connect()) as db:
            return self._read(db, draft_id, revision)

    def _read(self, db: object, draft_id: str, revision: int | None = None) -> dict | None:
        """Internal read using the caller's transaction after its access check."""
        latest = db.execute("SELECT MAX(revision) FROM sell_draft_revision WHERE draft_id = ?",
                            (draft_id,)).fetchone()[0]
        if latest is None:
            return None
        selected = latest if revision is None else revision
        row = db.execute("""SELECT r.*, d.handoff_id AS parent_handoff_id,
            x.reason AS rejection_reason FROM sell_draft_revision r
            JOIN sell_draft d ON d.id = r.draft_id
            LEFT JOIN sell_draft_rejection x ON x.draft_id = r.draft_id
                AND x.revision = r.revision
            WHERE r.draft_id = ? AND r.revision = ?""", (draft_id, selected)).fetchone()
        if row is None:
            return None
        result = dict(row)
        handoff = self.handoffs._read(db, row["handoff_id"])
        current = (selected == latest and handoff is not None
                   and handoff["status"] == "CURRENT_RESEARCH"
                   and handoff["revision"] == row["handoff_revision"]
                   and handoff["profile_sha256"] == row["profile_sha256"]
                   and handoff["route_id"] == row["recipient_route_id"]
                   and handoff["route_value"] == row["recipient_value"])
        result["status"] = ("REJECTED" if row["rejection_reason"] else
                            "CURRENT_DRAFT" if current else "REVIEW_REQUIRED")
        if not current:
            result["recipient_value"] = None
        result["claim_refs"] = json.loads(result.pop("claim_refs_json"))
        result["warnings"] = json.loads(result.pop("warnings_json"))
        fit = db.execute("""SELECT explanation, checks_json, outcome
            FROM buyer_fit_decision WHERE sequence = ?""",
            (row["fit_sequence"],)).fetchone()
        result["buyer_fit"] = ({"explanation": fit["explanation"],
                                "checks": json.loads(fit["checks_json"]),
                                "outcome": fit["outcome"]} if fit else None)
        result["candidate_evidence_ids"] = (result["buyer_fit"]["checks"]
                                            .get("cited_evidence_ids", [])
                                            if result["buyer_fit"] else [])
        source = db.execute("""SELECT h.product_id, r.profile_revision
            FROM find_handoff h JOIN find_handoff_revision r ON r.handoff_id = h.id
            WHERE h.id = ? AND r.revision = ?""",
            (row["handoff_id"], row["handoff_revision"])).fetchone()
        profile = db.execute("""SELECT payload_json FROM product_profile_revision
            WHERE product_id = ? AND revision = ? AND payload_sha256 = ?""",
            (source["product_id"], source["profile_revision"],
             row["profile_sha256"])).fetchone() if source else None
        result["permitted_claims"] = ([item for item in json.loads(profile["payload_json"])
                                       .get("claims", []) if item["confirmed_use"]]
                                      if profile else [])
        result["send_allowed"] = False
        result["approval_valid"] = False
        return result
