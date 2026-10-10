BEGIN IMMEDIATE;

-- An opt-out review has an explicit append-only event; no referenced table rebuild.
CREATE TABLE IF NOT EXISTS sell_inbound_opt_out (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    inbound_id TEXT NOT NULL REFERENCES sell_inbound_message(id),
    review_sequence INTEGER NOT NULL REFERENCES sell_inbound_review(sequence),
    route_id TEXT REFERENCES discovered_contact_route(id),
    explanation TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sell_inbound_opt_out_latest ON sell_inbound_opt_out(inbound_id, sequence DESC);
CREATE TRIGGER IF NOT EXISTS sell_inbound_opt_out_no_update BEFORE UPDATE ON sell_inbound_opt_out
BEGIN SELECT RAISE(ABORT, 'opt-out events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS sell_inbound_opt_out_no_delete BEFORE DELETE ON sell_inbound_opt_out
BEGIN SELECT RAISE(ABORT, 'opt-out events are append-only'); END;

PRAGMA user_version = 25;
COMMIT;
