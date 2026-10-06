"""Operator-attested phone/form contact; this module never performs an action."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from pilot_engine.find_handoff import FindHandoff
from pilot_engine.store import PilotStore, _utc_now


KINDS = {"SWITCHBOARD": "PHONE", "NAMED_PHONE": "PHONE",
         "CONTACT_FORM": "CONTACT_FORM"}


def _text(value: str, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > limit:
        raise ValueError(f"Invalid {label}")
    return value


def _time(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("UTC time required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("UTC time required") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("UTC time required")
    return parsed.isoformat(timespec="seconds")


class ManualContact:
    def __init__(self, store: PilotStore):
        self.store = store
        self.handoffs = FindHandoff(store)

    def open_opportunity(self, handoff_id: str) -> str:
        actor = self.store._require_access("PLAN_MANUAL_CONTACT", handoff_id)
        with self.store._transaction() as db:
            handoff = self.handoffs._read(db, handoff_id)
            if handoff is None or handoff["status"] != "CURRENT_RESEARCH":
                raise ValueError("Current Find handoff is required")
            if handoff["data_origin"] != "SYNTHETIC":
                raise ValueError("Real manual contact needs pilot readiness")
            prior = db.execute("""SELECT id FROM sell_opportunity
                WHERE product_id = ? AND candidate_id = ?""",
                (handoff["product_id"], handoff["candidate_id"])).fetchone()
            if prior:
                return prior["id"]
            oid = f"SO-{uuid4()}"
            db.execute("""INSERT INTO sell_opportunity
                (id, product_id, candidate_id, originating_handoff_id, actor_id, created_at_utc)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (oid, handoff["product_id"], handoff["candidate_id"], handoff_id,
                 actor, _utc_now()))
            return oid

    def plan(self, opportunity_id: str, handoff_id: str, *, operation_key: str,
             expected_destination: str, summary: str, planned_at_utc: str,
             reviewed_claims: bool) -> str:
        actor = self.store._require_access("PLAN_MANUAL_CONTACT", opportunity_id)
        operation_key = _text(operation_key, "operation key", 120)
        expected_destination = _text(expected_destination, "destination", 2048)
        summary = _text(summary, "action summary", 2000)
        planned_at_utc = _time(planned_at_utc)
        if reviewed_claims is not True:
            raise ValueError("Operator must review contact permission and claims")
        with self.store._transaction() as db:
            handoff = self.handoffs._read(db, handoff_id)
            if handoff is None or handoff["status"] != "CURRENT_RESEARCH":
                raise ValueError("Current Find handoff is required")
            route = db.execute("SELECT kind FROM discovered_contact_route WHERE id = ?",
                               (handoff["route_id"],)).fetchone()
            if route is None or route["kind"] not in KINDS:
                raise ValueError("Manual action requires a verified phone or form route")
            if handoff["route_value"] != expected_destination:
                raise ValueError("Destination changed; review the current route")
            opportunity = db.execute("SELECT * FROM sell_opportunity WHERE id = ?",
                                     (opportunity_id,)).fetchone()
            if (opportunity is None or (opportunity["product_id"], opportunity["candidate_id"])
                    != (handoff["product_id"], handoff["candidate_id"])):
                raise ValueError("Handoff does not belong to the Sell opportunity")
            existing = db.execute("SELECT * FROM manual_contact_action WHERE operation_key = ?",
                                  (operation_key,)).fetchone()
            if existing:
                if (existing["opportunity_id"], existing["handoff_id"],
                        existing["handoff_revision"], existing["destination_value"],
                        existing["summary"], existing["planned_at_utc"]) != (
                        opportunity_id, handoff_id, handoff["revision"], expected_destination,
                        summary, planned_at_utc):
                    raise ValueError("Operation key cannot be reused for a different action")
                return existing["id"]
            action_id = f"MC-{uuid4()}"
            db.execute("""INSERT INTO manual_contact_action
                (id, operation_key, opportunity_id, handoff_id, handoff_revision,
                 route_id, destination_value, channel, summary, planned_at_utc,
                 actor_id, created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (action_id, operation_key, opportunity_id, handoff_id, handoff["revision"],
                 handoff["route_id"], expected_destination, KINDS[route["kind"]],
                 summary, planned_at_utc, actor, _utc_now()))
            return action_id

    def record(self, action_id: str, *, event_key: str, outcome: str,
               occurred_at_utc: str, notes: str, next_step: str,
               performed_by_operator: bool) -> int:
        actor = self.store._require_access("RECORD_MANUAL_CONTACT", action_id)
        event_key = _text(event_key, "event key", 120)
        occurred_at_utc = _time(occurred_at_utc)
        if (outcome not in ("ATTEMPTED", "CONNECTED", "UNKNOWN")
                or not isinstance(notes, str) or len(notes) > 4000
                or not isinstance(next_step, str) or len(next_step) > 2000
                or performed_by_operator is not True):
            raise ValueError("Operator must attest the actual manual outcome")
        if datetime.fromisoformat(occurred_at_utc) > datetime.now(timezone.utc):
            raise ValueError("Actual action time cannot be in the future")
        with self.store._transaction() as db:
            action = db.execute("SELECT * FROM manual_contact_action WHERE id = ?",
                                (action_id,)).fetchone()
            if action is None:
                raise ValueError("Planned action does not exist")
            if action["channel"] == "CONTACT_FORM" and outcome == "CONNECTED":
                raise ValueError("Form submission is not a connected phone conversation")
            prior = db.execute("SELECT * FROM manual_contact_event WHERE event_key = ?",
                               (event_key,)).fetchone()
            if prior:
                if (prior["action_id"], prior["outcome"], prior["occurred_at_utc"],
                        prior["notes"], prior["next_step"]) != (
                        action_id, outcome, occurred_at_utc, notes, next_step):
                    raise ValueError("Event key cannot be reused for a different outcome")
                return prior["sequence"]
            latest = db.execute("""SELECT outcome FROM manual_contact_event
                WHERE action_id = ? ORDER BY sequence DESC LIMIT 1""", (action_id,)).fetchone()
            if latest and latest["outcome"] == "CONNECTED":
                raise ValueError("Connected action is final; use a new action for another contact")
            cur = db.execute("""INSERT INTO manual_contact_event
                (event_key, action_id, outcome, occurred_at_utc, notes, next_step,
                 actor_id, recorded_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_key, action_id, outcome, occurred_at_utc, notes, next_step,
                 actor, _utc_now()))
            return cur.lastrowid

    def read(self, action_id: str) -> dict | None:
        self.store._require_access("READ_MANUAL_CONTACT", action_id)
        with closing(self.store._connect()) as db:
            action = db.execute("SELECT * FROM manual_contact_action WHERE id = ?",
                                (action_id,)).fetchone()
            if action is None:
                return None
            result = dict(action)
            event = db.execute("""SELECT * FROM manual_contact_event WHERE action_id = ?
                ORDER BY sequence DESC LIMIT 1""", (action_id,)).fetchone()
            result["status"] = event["outcome"] if event else "PLANNED"
            result["latest_event"] = dict(event) if event else None
            result["events"] = [dict(row) for row in db.execute("""SELECT * FROM manual_contact_event
                WHERE action_id = ? ORDER BY sequence""", (action_id,))]
            handoff = self.handoffs._read(db, action["handoff_id"])
            current = (handoff is not None and handoff["status"] == "CURRENT_RESEARCH"
                       and handoff["revision"] == action["handoff_revision"]
                       and handoff["route_value"] == action["destination_value"])
            result["route_status"] = "CURRENT_RESEARCH" if current else "REVIEW_REQUIRED"
            if not current:
                result["destination_value"] = None
            result["performed_by_system"] = False
            return result
