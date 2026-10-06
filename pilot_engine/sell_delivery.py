"""Exact synthetic send approvals and durable mock attempt reconciliation."""

from __future__ import annotations

import hashlib
from contextlib import closing
from dataclasses import dataclass
from uuid import uuid4

from pilot_engine.sell_drafts import SellDrafts
from pilot_engine.store import PilotStore, _json, _utc_now


EMPTY_ATTACHMENT_SHA256 = hashlib.sha256(b"").hexdigest()


def _envelope_hash(draft: dict) -> str:
    fields = {key: draft[key] for key in ("recipient_value", "recipient_route_id", "channel",
                                          "sender_name", "sender_address", "language",
                                          "subject", "body", "revision")}
    fields["attachment_sha256"] = EMPTY_ATTACHMENT_SHA256
    return hashlib.sha256(_json(fields).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MockResult:
    outcome: str
    message_id: str | None = None


class CaptureMailbox:
    """An in-memory test double. CAPTURED is never evidence of actual delivery."""

    def __init__(self):
        self.messages: dict[str, dict] = {}
        self.unknown_keys: set[str] = set()

    def send(self, key: str, envelope: dict) -> MockResult:
        if key in self.unknown_keys:
            return MockResult("UNKNOWN")
        if key in self.messages:
            raise RuntimeError("Provider key was already used; reconcile before any repeat")
        self.messages[key] = dict(envelope)
        return MockResult("CAPTURED_NOT_DELIVERED", f"MOCK-{key}")


class SellDelivery:
    def __init__(self, store: PilotStore):
        self.store = store
        self.drafts = SellDrafts(store)

    def preview(self, draft_id: str, revision: int) -> dict:
        self.store._require_access("READ_SELL_DELIVERY", draft_id)
        with closing(self.store._connect()) as db:
            draft = self.drafts._read(db, draft_id, revision)
            if draft is None:
                raise ValueError("Draft revision does not exist")
            return {"draft_id": draft_id, "revision": revision,
                    "status": draft["status"], "recipient": draft["recipient_value"],
                    "sender_name": draft["sender_name"],
                    "sender_address": draft["sender_address"],
                    "subject": draft["subject"], "body": draft["body"],
                    "attachment_sha256": EMPTY_ATTACHMENT_SHA256,
                    "envelope_sha256": (_envelope_hash(draft)
                                        if draft["recipient_value"] else None)}

    def decide(self, draft_id: str, revision: int, decision: str, reason: str,
               *, expected_envelope_sha256: str | None = None,
               reviewed_claims: bool = False) -> int:
        actor = self.store._require_access("APPROVE_SELL_SEND", draft_id)
        if decision not in ("APPROVE", "REVOKE") or not isinstance(reason, str) or not reason.strip():
            raise ValueError("Approval or revocation needs a reason")
        with self.store._transaction() as db:
            draft = self.drafts._read(db, draft_id, revision)
            if draft is None:
                raise ValueError("Draft revision does not exist")
            prior = db.execute("""SELECT decision FROM sell_send_decision
                WHERE draft_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
                (draft_id, revision)).fetchone()
            if decision == "APPROVE":
                if (draft["status"] != "CURRENT_DRAFT" or draft["channel"] != "EMAIL"
                        or not reviewed_claims or any(w.startswith("UNCONFIRMED_APPLICATION:")
                                                       for w in draft["warnings"])):
                    raise ValueError("Current email draft and explicit claim review required")
                if expected_envelope_sha256 != _envelope_hash(draft):
                    raise ValueError("Approval must match the exact previewed envelope hash")
                if prior is not None and prior["decision"] == "APPROVE":
                    raise ValueError("This exact draft revision is already approved")
                if db.execute("""SELECT 1 FROM sell_send_attempt WHERE draft_id = ?
                    AND revision = ?""", (draft_id, revision)).fetchone():
                    raise ValueError("An attempted message cannot be reapproved")
            elif prior is None or prior["decision"] != "APPROVE":
                raise ValueError("Only an effective approval can be revoked")
            digest = _envelope_hash(draft) if draft["recipient_value"] else (
                db.execute("""SELECT envelope_sha256 FROM sell_send_decision
                    WHERE draft_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
                    (draft_id, revision)).fetchone()[0])
            cur = db.execute("""INSERT INTO sell_send_decision
                (draft_id, revision, decision, envelope_sha256, attachment_sha256,
                 reason, actor_id, decided_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (draft_id, revision, decision, digest, EMPTY_ATTACHMENT_SHA256,
                 reason.strip(), actor, _utc_now()))
            return cur.lastrowid

    def dispatch_synthetic(self, draft_id: str, revision: int, mailbox: CaptureMailbox) -> dict:
        """One mock capture at most; no SMTP provider or real contact is reachable."""
        actor = self.store._require_access("DISPATCH_SYNTHETIC_MAIL", draft_id)
        if type(mailbox) is not CaptureMailbox:
            raise ValueError("Only the in-memory capture mailbox is allowed")
        with self.store._transaction() as db:
            existing = db.execute("""SELECT id FROM sell_send_attempt WHERE draft_id = ?
                AND revision = ?""", (draft_id, revision)).fetchone()
            if existing:
                return self._status(db, existing["id"])
            draft = self.drafts._read(db, draft_id, revision)
            if draft is None or draft["status"] != "CURRENT_DRAFT" or draft["channel"] != "EMAIL":
                raise ValueError("Current approved email draft is required")
            if not draft["recipient_value"].endswith(("@example.org", "@example.com", "@example.net")):
                raise ValueError("Synthetic capture requires a reserved example recipient")
            decision = db.execute("""SELECT * FROM sell_send_decision WHERE draft_id = ?
                AND revision = ? ORDER BY sequence DESC LIMIT 1""", (draft_id, revision)).fetchone()
            if (decision is None or decision["decision"] != "APPROVE"
                    or decision["envelope_sha256"] != _envelope_hash(draft)
                    or decision["attachment_sha256"] != EMPTY_ATTACHMENT_SHA256):
                raise ValueError("Effective exact-content approval is required")
            attempt_id = f"SA-{uuid4()}"
            provider_key = f"sell:{draft_id}:{revision}"
            db.execute("""INSERT INTO sell_send_attempt
                (id, draft_id, revision, decision_sequence, envelope_sha256,
                 provider_key, actor_id, started_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (attempt_id, draft_id, revision, decision["sequence"],
                 decision["envelope_sha256"], provider_key, actor, _utc_now()))
            envelope = {key: draft[key] for key in ("recipient_value", "sender_name",
                      "sender_address", "subject", "body")}
        # The durable attempt precedes the side effect. A crash here stays UNKNOWN.
        try:
            result = mailbox.send(provider_key, envelope)
            if result.outcome not in ("CAPTURED_NOT_DELIVERED", "UNKNOWN", "REJECTED"):
                raise ValueError("Unsupported mock provider result")
        except Exception:
            result = MockResult("UNKNOWN")
        with self.store._transaction() as db:
            db.execute("""INSERT INTO sell_send_result
                (attempt_id, outcome, provider_message_id, provider_ref, actor_id, recorded_at_utc)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (attempt_id, result.outcome, result.message_id, None, actor, _utc_now()))
            return self._status(db, attempt_id)

    def reconcile(self, attempt_id: str, *, outcome: str, provider_ref: str,
                  provider_message_id: str | None = None) -> dict:
        actor = self.store._require_access("RECONCILE_SELL_DELIVERY", attempt_id)
        if (outcome not in ("PROVIDER_CONFIRMED", "PROVIDER_NOT_FOUND")
                or not isinstance(provider_ref, str) or not provider_ref.strip()):
            raise ValueError("Provider reconciliation needs a documented result")
        if outcome == "PROVIDER_CONFIRMED" and not provider_message_id:
            raise ValueError("Confirmation needs a provider message ID")
        with self.store._transaction() as db:
            state = self._status(db, attempt_id)
            if state["outcome"] != "UNKNOWN":
                raise ValueError("Only an unknown attempt can be reconciled")
            db.execute("""INSERT INTO sell_send_result
                (attempt_id, outcome, provider_message_id, provider_ref, actor_id, recorded_at_utc)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (attempt_id, outcome, provider_message_id, provider_ref.strip(), actor, _utc_now()))
            return self._status(db, attempt_id)

    @staticmethod
    def _status(db: object, attempt_id: str) -> dict:
        attempt = db.execute("SELECT * FROM sell_send_attempt WHERE id = ?", (attempt_id,)).fetchone()
        if attempt is None:
            raise ValueError("Attempt does not exist")
        result = db.execute("""SELECT * FROM sell_send_result WHERE attempt_id = ?
            ORDER BY sequence DESC LIMIT 1""", (attempt_id,)).fetchone()
        return {"id": attempt_id, "draft_id": attempt["draft_id"],
                "revision": attempt["revision"], "provider_key": attempt["provider_key"],
                "started_at_utc": attempt["started_at_utc"],
                "outcome": result["outcome"] if result else "UNKNOWN",
                "provider_message_id": result["provider_message_id"] if result else None,
                "provider_ref": result["provider_ref"] if result else None,
                "result_at_utc": result["recorded_at_utc"] if result else None,
                "delivered": False}

    def read(self, attempt_id: str) -> dict:
        self.store._require_access("READ_SELL_DELIVERY", attempt_id)
        with closing(self.store._connect()) as db:
            return self._status(db, attempt_id)
