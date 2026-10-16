BEGIN IMMEDIATE;

-- Independent extension version: legacy PRAGMA user_version remains 29.
-- No reference below points at a legacy/synthetic table.
CREATE TABLE live_schema_migration (
    version INTEGER PRIMARY KEY CHECK(version > 0),
    backup_sha256 TEXT NOT NULL,
    legacy_fingerprint TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL
);
CREATE TABLE live_pilot (
    id TEXT PRIMARY KEY CHECK(id GLOB 'LIVE-*'),
    manufacturer_name TEXT NOT NULL CHECK(length(trim(manufacturer_name)) > 0),
    authorization_ref TEXT NOT NULL CHECK(length(trim(authorization_ref)) > 0
        AND authorization_ref NOT GLOB 'SYN-*'),
    operator_name TEXT NOT NULL CHECK(length(trim(operator_name)) > 0),
    approver_name TEXT NOT NULL CHECK(length(trim(approver_name)) > 0),
    data_origin TEXT NOT NULL CHECK(data_origin = 'LIVE'),
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);
CREATE TABLE live_source (
    id TEXT PRIMARY KEY CHECK(id GLOB 'LIVE-*'),
    pilot_id TEXT NOT NULL REFERENCES live_pilot(id),
    kind TEXT NOT NULL CHECK(kind IN
        ('PRODUCT_PROFILE', 'PRICE_AUTHORITY', 'BUYER_SOURCE', 'CUSTOMER_PO')),
    source_ref TEXT NOT NULL CHECK(length(trim(source_ref)) > 0 AND source_ref NOT GLOB 'SYN-*'),
    source_sha256 TEXT NOT NULL CHECK(length(source_sha256) = 64
        AND source_sha256 NOT GLOB '*[^0-9a-f]*'),
    byte_length INTEGER NOT NULL CHECK(byte_length > 0),
    data_origin TEXT NOT NULL CHECK(data_origin = 'LIVE'),
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(id, pilot_id),
    UNIQUE(pilot_id, source_ref)
);
CREATE TABLE live_intake (
    id TEXT PRIMARY KEY CHECK(id GLOB 'LIVE-*'),
    pilot_id TEXT NOT NULL REFERENCES live_pilot(id),
    kind TEXT NOT NULL CHECK(kind IN
        ('PRODUCT_PROFILE', 'PRICE_AUTHORITY', 'BUYER_SOURCE', 'CUSTOMER_PO')),
    UNIQUE(id, pilot_id)
);
CREATE TABLE live_intake_revision (
    intake_id TEXT NOT NULL,
    pilot_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    source_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64
        AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
    data_origin TEXT NOT NULL CHECK(data_origin = 'LIVE'),
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    PRIMARY KEY(intake_id, revision),
    UNIQUE(intake_id, revision, payload_sha256),
    FOREIGN KEY(intake_id, pilot_id) REFERENCES live_intake(id, pilot_id),
    FOREIGN KEY(source_id, pilot_id) REFERENCES live_source(id, pilot_id)
);
CREATE TABLE live_confirmation (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    intake_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    payload_sha256 TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('APPROVE', 'REJECT', 'REVOKE')),
    approver_name TEXT NOT NULL CHECK(length(trim(approver_name)) > 0),
    authority_ref TEXT NOT NULL CHECK(length(trim(authority_ref)) > 0),
    method TEXT NOT NULL CHECK(length(trim(method)) > 0),
    evidence_ref TEXT NOT NULL CHECK(length(trim(evidence_ref)) > 0
        AND evidence_ref NOT GLOB 'SYN-*'),
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    confirmed_at_utc TEXT NOT NULL,
    recorded_by_actor TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    identity_basis TEXT NOT NULL CHECK(identity_basis = 'OPERATOR_ATTESTATION'),
    FOREIGN KEY(intake_id, revision, payload_sha256)
        REFERENCES live_intake_revision(intake_id, revision, payload_sha256)
);
CREATE INDEX live_confirmation_latest ON live_confirmation(intake_id, revision, sequence DESC);

COMMIT;
