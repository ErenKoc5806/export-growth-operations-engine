BEGIN IMMEDIATE;

CREATE TABLE sell_send_decision (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('APPROVE', 'REVOKE')),
    envelope_sha256 TEXT NOT NULL,
    attachment_sha256 TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    decided_at_utc TEXT NOT NULL,
    FOREIGN KEY(draft_id, revision) REFERENCES sell_draft_revision(draft_id, revision)
);
CREATE INDEX sell_send_decision_latest ON sell_send_decision(draft_id, revision, sequence DESC);
CREATE TABLE sell_send_attempt (
    id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    decision_sequence INTEGER NOT NULL REFERENCES sell_send_decision(sequence),
    envelope_sha256 TEXT NOT NULL,
    provider_key TEXT NOT NULL UNIQUE,
    actor_id TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    UNIQUE(draft_id, revision),
    FOREIGN KEY(draft_id, revision) REFERENCES sell_draft_revision(draft_id, revision)
);
CREATE TABLE sell_send_result (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id TEXT NOT NULL REFERENCES sell_send_attempt(id),
    outcome TEXT NOT NULL CHECK(outcome IN
        ('CAPTURED_NOT_DELIVERED', 'UNKNOWN', 'REJECTED', 'PROVIDER_CONFIRMED', 'PROVIDER_NOT_FOUND')),
    provider_message_id TEXT,
    provider_ref TEXT,
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);
CREATE TRIGGER sell_send_decision_no_update BEFORE UPDATE ON sell_send_decision
BEGIN SELECT RAISE(ABORT, 'send decisions are append-only'); END;
CREATE TRIGGER sell_send_decision_no_delete BEFORE DELETE ON sell_send_decision
BEGIN SELECT RAISE(ABORT, 'send decisions are append-only'); END;
CREATE TRIGGER sell_send_attempt_no_update BEFORE UPDATE ON sell_send_attempt
BEGIN SELECT RAISE(ABORT, 'send attempts are append-only'); END;
CREATE TRIGGER sell_send_attempt_no_delete BEFORE DELETE ON sell_send_attempt
BEGIN SELECT RAISE(ABORT, 'send attempts are append-only'); END;
CREATE TRIGGER sell_send_result_no_update BEFORE UPDATE ON sell_send_result
BEGIN SELECT RAISE(ABORT, 'send results are append-only'); END;
CREATE TRIGGER sell_send_result_no_delete BEFORE DELETE ON sell_send_result
BEGIN SELECT RAISE(ABORT, 'send results are append-only'); END;

PRAGMA user_version = 15;
COMMIT;
