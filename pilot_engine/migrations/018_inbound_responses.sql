BEGIN IMMEDIATE;

CREATE TABLE sell_provider_observation (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id TEXT NOT NULL REFERENCES sell_send_attempt(id),
    source_system TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    provider_message_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('ACCEPTED', 'BOUNCED', 'UNKNOWN')),
    observed_at_utc TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(source_system, source_ref)
);
CREATE TABLE sell_inbound_message (
    id TEXT PRIMARY KEY,
    source_system TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    raw_ref TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    channel TEXT NOT NULL CHECK(channel IN ('EMAIL', 'PHONE', 'CONTACT_FORM')),
    provider_message_id TEXT,
    conversation_id TEXT,
    matched_opportunity_id TEXT REFERENCES sell_opportunity(id),
    match_status TEXT NOT NULL CHECK(match_status IN ('MATCHED', 'REVIEW_REQUIRED')),
    data_origin TEXT NOT NULL CHECK(data_origin = 'SYNTHETIC'),
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(source_system, source_ref)
);
CREATE TABLE sell_inbound_review (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    inbound_id TEXT NOT NULL REFERENCES sell_inbound_message(id),
    opportunity_id TEXT NOT NULL REFERENCES sell_opportunity(id),
    classification TEXT NOT NULL CHECK(classification IN
        ('INTEREST', 'REJECTION', 'RFQ_CANDIDATE', 'OTHER')),
    evidence_ref TEXT NOT NULL,
    explanation TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    reviewed_at_utc TEXT NOT NULL
);
CREATE INDEX sell_inbound_review_latest ON sell_inbound_review(inbound_id, sequence DESC);
CREATE TRIGGER sell_provider_observation_no_update BEFORE UPDATE ON sell_provider_observation
BEGIN SELECT RAISE(ABORT, 'provider observations are append-only'); END;
CREATE TRIGGER sell_provider_observation_no_delete BEFORE DELETE ON sell_provider_observation
BEGIN SELECT RAISE(ABORT, 'provider observations are append-only'); END;
CREATE TRIGGER sell_inbound_message_no_update BEFORE UPDATE ON sell_inbound_message
BEGIN SELECT RAISE(ABORT, 'inbound messages are immutable'); END;
CREATE TRIGGER sell_inbound_message_no_delete BEFORE DELETE ON sell_inbound_message
BEGIN SELECT RAISE(ABORT, 'inbound messages are immutable'); END;
CREATE TRIGGER sell_inbound_review_no_update BEFORE UPDATE ON sell_inbound_review
BEGIN SELECT RAISE(ABORT, 'inbound reviews are append-only'); END;
CREATE TRIGGER sell_inbound_review_no_delete BEFORE DELETE ON sell_inbound_review
BEGIN SELECT RAISE(ABORT, 'inbound reviews are append-only'); END;

PRAGMA user_version = 18;
COMMIT;
