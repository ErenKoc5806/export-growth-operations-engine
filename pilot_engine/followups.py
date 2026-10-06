"""Operator-owned follow-up reminders; scheduling never performs outreach."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from pilot_engine.find_handoff import FindHandoff
from pilot_engine.manual_contact import _text, _time
from pilot_engine.store import PilotStore, _utc_now


class Followups:
    def __init__(self, store: PilotStore):
        self.store = store
        self.handoffs = FindHandoff(store)

    def schedule(self, opportunity_id: str, source_action_id: str, *, operation_key: str,
                 due_at_utc: str, channel: str, reason: str,
                 owner_id: str | None = None) -> str:
        actor = self.store._require_access("PLAN_FOLLOWUP", opportunity_id)
        operation_key = _text(operation_key, "operation key", 120)
        due_at_utc = _time(due_at_utc)
        reason = _text(reason, "follow-up reason", 2000)
        owner_id = _text(owner_id or actor, "owner", 160)
        if channel not in ("PHONE", "CONTACT_FORM", "EMAIL"):
            raise ValueError("Unsupported follow-up channel")
        with self.store._transaction() as db:
            action = db.execute("SELECT * FROM manual_contact_action WHERE id = ?",
                                (source_action_id,)).fetchone()
            if action is None or action["opportunity_id"] != opportunity_id:
                raise ValueError("Source manual action must belong to the opportunity")
            source_result = db.execute("""SELECT outcome FROM manual_contact_event
                WHERE action_id = ? ORDER BY sequence DESC LIMIT 1""",
                (source_action_id,)).fetchone()
            if source_result is None or source_result["outcome"] not in ("ATTEMPTED", "CONNECTED"):
                raise ValueError("Follow-up requires a recorded, non-unknown source action")
            current = self.handoffs._read(db, action["handoff_id"])
            if (current is None or current["status"] != "CURRENT_RESEARCH"
                    or current["revision"] != action["handoff_revision"]
                    or current["route_id"] != action["route_id"]):
                raise ValueError("Current source-backed contact is required")
            if db.execute("SELECT 1 FROM sell_followup_stop WHERE opportunity_id = ?",
                          (opportunity_id,)).fetchone():
                raise ValueError("This opportunity has a stop decision")
            count = db.execute("""SELECT COUNT(*) FROM manual_contact_action
                WHERE opportunity_id = ?""", (opportunity_id,)).fetchone()[0]
            prior = db.execute("SELECT * FROM sell_followup WHERE operation_key = ?",
                               (operation_key,)).fetchone()
            if prior:
                if (prior["opportunity_id"], prior["source_action_id"], prior["due_at_utc"],
                        prior["channel"], prior["reason"], prior["owner_id"]) != (
                        opportunity_id, source_action_id, due_at_utc, channel, reason, owner_id):
                    raise ValueError("Operation key cannot be reused for another follow-up")
                return prior["id"]
            fid = f"FU-{uuid4()}"
            db.execute("""INSERT INTO sell_followup
                (id, operation_key, opportunity_id, source_action_id, handoff_id,
                 route_id, owner_id, due_at_utc, channel, reason, attempt_count,
                 actor_id, created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (fid, operation_key, opportunity_id, source_action_id, action["handoff_id"],
                 action["route_id"], owner_id, due_at_utc, channel, reason, count,
                 actor, _utc_now()))
            return fid

    def stop(self, opportunity_id: str, *, reason: str, evidence_ref: str,
             route_id: str | None = None) -> int:
        actor = self.store._require_access("RECORD_FOLLOWUP", opportunity_id)
        if reason not in ("REPLY", "OPT_OUT", "CLOSED"):
            raise ValueError("Unknown stop reason")
        evidence_ref = _text(evidence_ref, "stop evidence", 1000)
        with self.store._transaction() as db:
            if not db.execute("SELECT 1 FROM sell_opportunity WHERE id = ?",
                              (opportunity_id,)).fetchone():
                raise ValueError("Opportunity does not exist")
            if reason == "OPT_OUT":
                suppressed = db.execute("""SELECT 1 FROM discovered_contact_route r
                    JOIN sell_opportunity o ON o.product_id = r.product_id
                        AND o.candidate_id = r.candidate_id
                    WHERE o.id = ? AND r.id = ? AND r.suppressed = 1""",
                    (opportunity_id, route_id)).fetchone()
                if not suppressed:
                    raise ValueError("Opt-out needs a suppressed route in the Find list")
            prior = db.execute("""SELECT * FROM sell_followup_stop
                WHERE opportunity_id = ? ORDER BY sequence DESC LIMIT 1""",
                (opportunity_id,)).fetchone()
            if prior and (prior["reason"], prior["evidence_ref"]) == (reason, evidence_ref):
                return prior["sequence"]
            cur = db.execute("""INSERT INTO sell_followup_stop
                (opportunity_id, reason, evidence_ref, actor_id, recorded_at_utc)
                VALUES (?, ?, ?, ?, ?)""", (opportunity_id, reason, evidence_ref,
                                             actor, _utc_now()))
            return cur.lastrowid

    def _read(self, db: object, followup_id: str) -> dict | None:
        row = db.execute("SELECT * FROM sell_followup WHERE id = ?", (followup_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        events = [dict(event) for event in db.execute("""SELECT * FROM sell_followup_event
            WHERE followup_id = ? ORDER BY sequence""", (followup_id,))]
        result["events"] = events
        result["performed_by_system"] = False
        if events:
            result["status"] = events[-1]["outcome"]
            return result
        stop = db.execute("""SELECT reason, evidence_ref FROM sell_followup_stop
            WHERE opportunity_id = ? ORDER BY sequence DESC LIMIT 1""",
            (row["opportunity_id"],)).fetchone()
        if stop:
            result["status"] = "SUPPRESSED"
            result["stop_reason"] = stop["reason"]
            return result
        reviewed_reply = db.execute("""SELECT 1 FROM sell_inbound_review r
            WHERE r.opportunity_id = ? AND r.sequence =
                (SELECT MAX(x.sequence) FROM sell_inbound_review x
                 WHERE x.inbound_id = r.inbound_id) LIMIT 1""",
            (row["opportunity_id"],)).fetchone()
        if reviewed_reply:
            result["status"] = "SUPPRESSED"
            result["stop_reason"] = "REVIEWED_REPLY"
            return result
        handoff = self.handoffs._read(db, row["handoff_id"])
        if (handoff is None or handoff["status"] != "CURRENT_RESEARCH"
                or handoff["route_id"] != row["route_id"]):
            result["status"] = "BLOCKED_ROUTE"
            return result
        source = db.execute("""SELECT outcome FROM manual_contact_event
            WHERE action_id = ? ORDER BY sequence DESC LIMIT 1""",
            (row["source_action_id"],)).fetchone()
        if source is None or source["outcome"] == "UNKNOWN":
            result["status"] = "BLOCKED_UNKNOWN"
            return result
        due = datetime.fromisoformat(row["due_at_utc"])
        now = datetime.now(timezone.utc)
        result["status"] = ("SCHEDULED" if due > now else
                            "DUE" if due.date() == now.date() else "OVERDUE")
        return result

    def read(self, followup_id: str) -> dict | None:
        self.store._require_access("READ_FOLLOWUP", followup_id)
        with closing(self.store._connect()) as db:
            return self._read(db, followup_id)

    def list_due(self, owner_id: str) -> list[dict]:
        self.store._require_access("READ_FOLLOWUP", owner_id)
        with closing(self.store._connect()) as db:
            ids = [row[0] for row in db.execute("""SELECT id FROM sell_followup
                WHERE owner_id = ? ORDER BY due_at_utc, id""", (owner_id,))]
            return [view for fid in ids if (view := self._read(db, fid))["status"]
                    in ("DUE", "OVERDUE")]

    def conclude(self, followup_id: str, *, event_key: str, outcome: str, reason: str,
                 linked_action_id: str | None = None,
                 linked_send_attempt_id: str | None = None) -> int:
        actor = self.store._require_access("RECORD_FOLLOWUP", followup_id)
        event_key = _text(event_key, "event key", 120)
        reason = _text(reason, "conclusion reason", 2000)
        if outcome not in ("COMPLETED", "SKIPPED"):
            raise ValueError("Follow-up outcome must be completed or skipped")
        with self.store._transaction() as db:
            row = db.execute("SELECT * FROM sell_followup WHERE id = ?", (followup_id,)).fetchone()
            if row is None:
                raise ValueError("Follow-up does not exist")
            previous = db.execute("SELECT * FROM sell_followup_event WHERE event_key = ?",
                                  (event_key,)).fetchone()
            if previous:
                if (previous["followup_id"], previous["outcome"], previous["reason"],
                        previous["linked_action_id"], previous["linked_send_attempt_id"]) != (
                        followup_id, outcome, reason, linked_action_id, linked_send_attempt_id):
                    raise ValueError("Event key cannot be reused for another result")
                return previous["sequence"]
            view = self._read(db, followup_id)
            if view["status"] in ("COMPLETED", "SKIPPED"):
                raise ValueError("Follow-up already concluded")
            if outcome == "COMPLETED":
                if view["status"] not in ("SCHEDULED", "DUE", "OVERDUE"):
                    raise ValueError("Blocked follow-up cannot be completed")
                if row["channel"] == "EMAIL":
                    raise ValueError("Email completion needs a real provider and new exact approval")
                else:
                    if linked_send_attempt_id or not linked_action_id:
                        raise ValueError("Manual completion needs a separate operator action")
                    action = db.execute("SELECT * FROM manual_contact_action WHERE id = ?",
                                        (linked_action_id,)).fetchone()
                    event = db.execute("""SELECT outcome FROM manual_contact_event
                        WHERE action_id = ? ORDER BY sequence DESC LIMIT 1""",
                        (linked_action_id,)).fetchone()
                    if (action is None or action["id"] == row["source_action_id"]
                            or action["opportunity_id"] != row["opportunity_id"]
                            or action["channel"] != row["channel"]
                            or action["created_at_utc"] < row["created_at_utc"]
                            or event is None or event["outcome"] not in ("ATTEMPTED", "CONNECTED")):
                        raise ValueError("New operator action with recorded result is required")
            elif linked_action_id or linked_send_attempt_id:
                raise ValueError("Skipped follow-up cannot claim an action")
            cur = db.execute("""INSERT INTO sell_followup_event
                (event_key, followup_id, outcome, linked_action_id, linked_send_attempt_id,
                 reason, actor_id, recorded_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_key, followup_id, outcome, linked_action_id, linked_send_attempt_id,
                 reason, actor, _utc_now()))
            return cur.lastrowid
