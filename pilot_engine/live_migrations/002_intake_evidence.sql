BEGIN IMMEDIATE;

CREATE TABLE live_document (
    id TEXT PRIMARY KEY CHECK(id GLOB 'LIVE-*'),
    sha256 TEXT NOT NULL UNIQUE CHECK(length(sha256) = 64),
    content BLOB NOT NULL CHECK(length(content) BETWEEN 1 AND 5000000),
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);
CREATE TABLE live_authorization (
    pilot_id TEXT PRIMARY KEY REFERENCES live_pilot(id),
    manufacturer_document_id TEXT NOT NULL REFERENCES live_document(id),
    it_document_id TEXT NOT NULL REFERENCES live_document(id),
    operator_actor TEXT NOT NULL,
    approver_role TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL,
    scope_json TEXT NOT NULL CHECK(json_valid(scope_json)),
    identity_basis TEXT NOT NULL CHECK(identity_basis = 'OPERATOR_ATTESTATION')
);
CREATE TABLE live_source_permission (
    source_id TEXT PRIMARY KEY REFERENCES live_source(id),
    document_id TEXT NOT NULL REFERENCES live_document(id),
    permission_document_id TEXT NOT NULL REFERENCES live_document(id),
    permission_ref TEXT NOT NULL,
    terms_ref TEXT NOT NULL,
    processing_basis TEXT NOT NULL,
    retention_until_utc TEXT NOT NULL,
    reviewer_name TEXT NOT NULL,
    reviewed_at_utc TEXT NOT NULL
);
CREATE TABLE live_confirmation_evidence (
    confirmation_sequence INTEGER PRIMARY KEY REFERENCES live_confirmation(sequence),
    document_id TEXT NOT NULL REFERENCES live_document(id),
    approver_role TEXT NOT NULL
);
CREATE TABLE live_intake_stop (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    pilot_id TEXT NOT NULL REFERENCES live_pilot(id),
    source_id TEXT REFERENCES live_source(id),
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);

COMMIT;
