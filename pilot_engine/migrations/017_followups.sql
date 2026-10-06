BEGIN IMMEDIATE;

CREATE TABLE sell_followup (
    id TEXT PRIMARY KEY,
    operation_key TEXT NOT NULL UNIQUE,
    opportunity_id TEXT NOT NULL REFERENCES sell_opportunity(id),
    source_action_id TEXT NOT NULL REFERENCES manual_contact_action(id),
    handoff_id TEXT NOT NULL REFERENCES find_handoff(id),
    route_id TEXT NOT NULL REFERENCES discovered_contact_route(id),
    owner_id TEXT NOT NULL,
    due_at_utc TEXT NOT NULL,
    channel TEXT NOT NULL CHECK(channel IN ('PHONE', 'CONTACT_FORM', 'EMAIL')),
    reason TEXT NOT NULL,
    attempt_count INTEGER NOT NULL CHECK(attempt_count >= 1),
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
CREATE TABLE sell_followup_event (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    followup_id TEXT NOT NULL REFERENCES sell_followup(id),
    outcome TEXT NOT NULL CHECK(outcome IN ('COMPLETED', 'SKIPPED')),
    linked_action_id TEXT REFERENCES manual_contact_action(id),
    linked_send_attempt_id TEXT REFERENCES sell_send_attempt(id),
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);
CREATE TABLE sell_followup_stop (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id TEXT NOT NULL REFERENCES sell_opportunity(id),
    reason TEXT NOT NULL CHECK(reason IN ('REPLY', 'OPT_OUT', 'CLOSED')),
    evidence_ref TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);
CREATE INDEX sell_followup_due ON sell_followup(owner_id, due_at_utc);
CREATE TRIGGER sell_followup_no_update BEFORE UPDATE ON sell_followup
BEGIN SELECT RAISE(ABORT, 'follow-up plans are immutable'); END;
CREATE TRIGGER sell_followup_no_delete BEFORE DELETE ON sell_followup
BEGIN SELECT RAISE(ABORT, 'follow-up plans are immutable'); END;
CREATE TRIGGER sell_followup_event_no_update BEFORE UPDATE ON sell_followup_event
BEGIN SELECT RAISE(ABORT, 'follow-up events are append-only'); END;
CREATE TRIGGER sell_followup_event_no_delete BEFORE DELETE ON sell_followup_event
BEGIN SELECT RAISE(ABORT, 'follow-up events are append-only'); END;
CREATE TRIGGER sell_followup_stop_no_update BEFORE UPDATE ON sell_followup_stop
BEGIN SELECT RAISE(ABORT, 'follow-up stops are append-only'); END;
CREATE TRIGGER sell_followup_stop_no_delete BEFORE DELETE ON sell_followup_stop
BEGIN SELECT RAISE(ABORT, 'follow-up stops are append-only'); END;

PRAGMA user_version = 17;
COMMIT;
