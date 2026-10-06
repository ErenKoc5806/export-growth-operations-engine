BEGIN IMMEDIATE;

CREATE TABLE sell_rfq (
    id TEXT PRIMARY KEY,
    inbound_id TEXT NOT NULL UNIQUE REFERENCES sell_inbound_message(id),
    opportunity_id TEXT NOT NULL REFERENCES sell_opportunity(id),
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
CREATE TABLE sell_rfq_revision (
    rfq_id TEXT NOT NULL REFERENCES sell_rfq(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    inbound_review_sequence INTEGER NOT NULL REFERENCES sell_inbound_review(sequence),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL,
    missing_json TEXT NOT NULL CHECK(json_valid(missing_json)),
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY(rfq_id, revision)
);
CREATE TABLE sell_rfq_decision (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    rfq_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('ACCEPT', 'REVOKE')),
    payload_sha256 TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    decided_at_utc TEXT NOT NULL,
    FOREIGN KEY(rfq_id, revision) REFERENCES sell_rfq_revision(rfq_id, revision)
);
CREATE TRIGGER sell_rfq_no_update BEFORE UPDATE ON sell_rfq
BEGIN SELECT RAISE(ABORT, 'RFQ identity is immutable'); END;
CREATE TRIGGER sell_rfq_no_delete BEFORE DELETE ON sell_rfq
BEGIN SELECT RAISE(ABORT, 'RFQ identity is immutable'); END;
CREATE TRIGGER sell_rfq_revision_no_update BEFORE UPDATE ON sell_rfq_revision
BEGIN SELECT RAISE(ABORT, 'RFQ revisions are append-only'); END;
CREATE TRIGGER sell_rfq_revision_no_delete BEFORE DELETE ON sell_rfq_revision
BEGIN SELECT RAISE(ABORT, 'RFQ revisions are append-only'); END;
CREATE TRIGGER sell_rfq_decision_no_update BEFORE UPDATE ON sell_rfq_decision
BEGIN SELECT RAISE(ABORT, 'RFQ decisions are append-only'); END;
CREATE TRIGGER sell_rfq_decision_no_delete BEFORE DELETE ON sell_rfq_decision
BEGIN SELECT RAISE(ABORT, 'RFQ decisions are append-only'); END;

PRAGMA user_version = 19;
COMMIT;
