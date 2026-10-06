"""Synthetic provider observations and operator-reviewed inbound responses."""

from __future__ import annotations

from contextlib import closing
from uuid import uuid4

from pilot_engine.manual_contact import _text, _time
from pilot_engine.store import PilotStore, _utc_now


class InboundResponses:
    def __init__(self, store: PilotStore):
        self.store = store

    def record_provider(self, attempt_id: str, *, source_ref: str,
                        provider_message_id: str, outcome: str,
                        observed_at_utc: str) -> int:
        actor = self.store._require_access("RECORD_SYNTHETIC_RESPONSE", attempt_id)
        source_ref = _text(source_ref, "provider source reference", 300)
        provider_message_id = _text(provider_message_id, "provider message ID", 500)
        observed_at_utc = _time(observed_at_utc)
        if outcome not in ("ACCEPTED", "BOUNCED", "UNKNOWN"):
            raise ValueError("Unsupported provider outcome")
        with self.store._transaction() as db:
            attempt = db.execute("SELECT id FROM sell_send_attempt WHERE id = ?",
                                 (attempt_id,)).fetchone()
            result = db.execute("""SELECT provider_message_id FROM sell_send_result
                WHERE attempt_id = ? ORDER BY sequence DESC LIMIT 1""", (attempt_id,)).fetchone()
            if (attempt is None or result is None
                    or result["provider_message_id"] != provider_message_id
                    or not provider_message_id.startswith("MOCK-")):
                raise ValueError("Synthetic provider ID must match a recorded capture")
            prior = db.execute("""SELECT * FROM sell_provider_observation
                WHERE source_system = 'SYNTHETIC' AND source_ref = ?""",
                (source_ref,)).fetchone()
            if prior:
                if (prior["attempt_id"], prior["provider_message_id"],
                        prior["outcome"], prior["observed_at_utc"]) != (
                        attempt_id, provider_message_id, outcome, observed_at_utc):
                    raise ValueError("Provider source reference conflicts with its recorded event")
                return prior["sequence"]
            cur = db.execute("""INSERT INTO sell_provider_observation
                (attempt_id, source_system, source_ref, provider_message_id, outcome,
                 observed_at_utc, actor_id, recorded_at_utc)
                VALUES (?, 'SYNTHETIC', ?, ?, ?, ?, ?, ?)""",
                (attempt_id, source_ref, provider_message_id, outcome,
                 observed_at_utc, actor, _utc_now()))
            return cur.lastrowid

    def record_inbound(self, *, source_ref: str, raw_ref: str,
                       received_at_utc: str, channel: str,
                       provider_message_id: str | None = None,
                       conversation_id: str | None = None) -> str:
        actor = self.store._require_access("RECORD_SYNTHETIC_RESPONSE", source_ref)
        source_ref = _text(source_ref, "inbound source reference", 300)
        raw_ref = _text(raw_ref, "raw source reference", 1000)
        received_at_utc = _time(received_at_utc)
        if not raw_ref.startswith("SYN-") or channel not in ("EMAIL", "PHONE", "CONTACT_FORM"):
            raise ValueError("Only labeled synthetic inbound evidence is allowed")
        if provider_message_id is not None:
            provider_message_id = _text(provider_message_id, "provider message ID", 500)
        if conversation_id is not None:
            conversation_id = _text(conversation_id, "conversation ID", 500)
        with self.store._transaction() as db:
            matches = []
            if provider_message_id:
                matches = [row[0] for row in db.execute("""SELECT DISTINCT o.id
                    FROM sell_send_result r JOIN sell_send_attempt a ON a.id = r.attempt_id
                    JOIN sell_draft d ON d.id = a.draft_id
                    JOIN find_handoff h ON h.id = d.handoff_id
                    JOIN sell_opportunity o ON o.product_id = h.product_id
                        AND o.candidate_id = h.candidate_id
                    WHERE r.provider_message_id = ?""", (provider_message_id,))]
            matched = matches[0] if len(matches) == 1 else None
            prior = db.execute("""SELECT * FROM sell_inbound_message
                WHERE source_system = 'SYNTHETIC' AND source_ref = ?""",
                (source_ref,)).fetchone()
            if prior:
                if (prior["raw_ref"], prior["received_at_utc"], prior["channel"],
                        prior["provider_message_id"], prior["conversation_id"]) != (
                        raw_ref, received_at_utc, channel, provider_message_id, conversation_id):
                    raise ValueError("Inbound source reference conflicts with recorded evidence")
                return prior["id"]
            inbound_id = f"IN-{uuid4()}"
            db.execute("""INSERT INTO sell_inbound_message
                (id, source_system, source_ref, raw_ref, received_at_utc, channel,
                 provider_message_id, conversation_id, matched_opportunity_id,
                 match_status, data_origin, actor_id, recorded_at_utc)
                VALUES (?, 'SYNTHETIC', ?, ?, ?, ?, ?, ?, ?, ?, 'SYNTHETIC', ?, ?)""",
                (inbound_id, source_ref, raw_ref, received_at_utc, channel,
                 provider_message_id, conversation_id, matched,
                 "MATCHED" if matched else "REVIEW_REQUIRED", actor, _utc_now()))
            return inbound_id

    def review(self, inbound_id: str, opportunity_id: str, *, classification: str,
               evidence_ref: str, explanation: str) -> int:
        actor = self.store._require_access("REVIEW_SYNTHETIC_RESPONSE", inbound_id)
        evidence_ref = _text(evidence_ref, "evidence reference", 1000)
        explanation = _text(explanation, "review explanation", 2000)
        if classification not in ("INTEREST", "REJECTION", "RFQ_CANDIDATE", "OTHER"):
            raise ValueError("Unsupported response classification")
        with self.store._transaction() as db:
            inbound = db.execute("SELECT * FROM sell_inbound_message WHERE id = ?",
                                 (inbound_id,)).fetchone()
            opportunity = db.execute("SELECT id FROM sell_opportunity WHERE id = ?",
                                     (opportunity_id,)).fetchone()
            if inbound is None or opportunity is None:
                raise ValueError("Inbound evidence and opportunity are required")
            if (inbound["matched_opportunity_id"] is not None
                    and inbound["matched_opportunity_id"] != opportunity_id):
                raise ValueError("Provider correlation points to another opportunity")
            prior = db.execute("""SELECT * FROM sell_inbound_review WHERE inbound_id = ?
                ORDER BY sequence DESC LIMIT 1""", (inbound_id,)).fetchone()
            if prior and (prior["opportunity_id"], prior["classification"],
                          prior["evidence_ref"], prior["explanation"]) == (
                    opportunity_id, classification, evidence_ref, explanation):
                return prior["sequence"]
            cur = db.execute("""INSERT INTO sell_inbound_review
                (inbound_id, opportunity_id, classification, evidence_ref,
                 explanation, actor_id, reviewed_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (inbound_id, opportunity_id, classification, evidence_ref,
                 explanation, actor, _utc_now()))
            return cur.lastrowid

    def read(self, inbound_id: str) -> dict | None:
        self.store._require_access("READ_SYNTHETIC_RESPONSE", inbound_id)
        with closing(self.store._connect()) as db:
            row = db.execute("SELECT * FROM sell_inbound_message WHERE id = ?",
                             (inbound_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["reviews"] = [dict(x) for x in db.execute("""SELECT * FROM sell_inbound_review
                WHERE inbound_id = ? ORDER BY sequence""", (inbound_id,))]
            latest = result["reviews"][-1] if result["reviews"] else None
            result["classification"] = latest["classification"] if latest else "UNREVIEWED"
            result["opportunity_id"] = latest["opportunity_id"] if latest else row["matched_opportunity_id"]
            result["rfq_created"] = False
            result["reviewed_synthetic_response"] = latest is not None
            return result

    def opportunity_summary(self, opportunity_id: str) -> dict:
        self.store._require_access("READ_SYNTHETIC_RESPONSE", opportunity_id)
        with closing(self.store._connect()) as db:
            if not db.execute("SELECT 1 FROM sell_opportunity WHERE id = ?",
                              (opportunity_id,)).fetchone():
                raise ValueError("Opportunity does not exist")
            reviews = [dict(row) for row in db.execute("""SELECT r.* FROM sell_inbound_review r
                WHERE r.opportunity_id = ? AND r.sequence =
                    (SELECT MAX(x.sequence) FROM sell_inbound_review x
                     WHERE x.inbound_id = r.inbound_id)
                ORDER BY r.sequence""", (opportunity_id,))]
            return {"opportunity_id": opportunity_id, "reviewed_responses": reviews,
                    "response_status": ("REVIEWED_RESPONSE" if reviews else
                                        "NO_RESPONSE_OBSERVED"),
                    "no_response_is_rejection": False}
