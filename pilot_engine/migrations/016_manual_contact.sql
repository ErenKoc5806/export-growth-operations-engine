BEGIN IMMEDIATE;

CREATE TABLE sell_opportunity (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES product(id),
    candidate_id TEXT NOT NULL REFERENCES buyer_candidate(id),
    originating_handoff_id TEXT NOT NULL REFERENCES find_handoff(id),
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE(product_id, candidate_id)
);
CREATE TABLE manual_contact_action (
    id TEXT PRIMARY KEY,
    operation_key TEXT NOT NULL UNIQUE,
    opportunity_id TEXT NOT NULL REFERENCES sell_opportunity(id),
    handoff_id TEXT NOT NULL,
    handoff_revision INTEGER NOT NULL,
    route_id TEXT NOT NULL REFERENCES discovered_contact_route(id),
    destination_value TEXT NOT NULL,
    channel TEXT NOT NULL CHECK(channel IN ('PHONE', 'CONTACT_FORM')),
    summary TEXT NOT NULL,
    planned_at_utc TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY(handoff_id, handoff_revision) REFERENCES find_handoff_revision(handoff_id, revision)
);
CREATE TABLE manual_contact_event (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    action_id TEXT NOT NULL REFERENCES manual_contact_action(id),
    outcome TEXT NOT NULL CHECK(outcome IN ('ATTEMPTED', 'CONNECTED', 'UNKNOWN')),
    occurred_at_utc TEXT NOT NULL,
    notes TEXT NOT NULL,
    next_step TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);
CREATE INDEX manual_contact_event_latest ON manual_contact_event(action_id, sequence DESC);
CREATE TRIGGER sell_opportunity_no_update BEFORE UPDATE ON sell_opportunity
BEGIN SELECT RAISE(ABORT, 'sell opportunity identity is immutable'); END;
CREATE TRIGGER sell_opportunity_no_delete BEFORE DELETE ON sell_opportunity
BEGIN SELECT RAISE(ABORT, 'sell opportunity identity is immutable'); END;
CREATE TRIGGER manual_action_no_update BEFORE UPDATE ON manual_contact_action
BEGIN SELECT RAISE(ABORT, 'manual action is immutable'); END;
CREATE TRIGGER manual_action_no_delete BEFORE DELETE ON manual_contact_action
BEGIN SELECT RAISE(ABORT, 'manual action is immutable'); END;
CREATE TRIGGER manual_event_no_update BEFORE UPDATE ON manual_contact_event
BEGIN SELECT RAISE(ABORT, 'manual events are append-only'); END;
CREATE TRIGGER manual_event_no_delete BEFORE DELETE ON manual_contact_event
BEGIN SELECT RAISE(ABORT, 'manual events are append-only'); END;

PRAGMA user_version = 16;
COMMIT;
