BEGIN IMMEDIATE;

CREATE TABLE sell_price_authority (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES product(id),
    source_ref TEXT NOT NULL UNIQUE,
    source_sha256 TEXT NOT NULL,
    byte_length INTEGER NOT NULL CHECK(byte_length > 0),
    sku TEXT NOT NULL,
    currency TEXT NOT NULL,
    unit_price_text TEXT NOT NULL,
    valid_until_utc TEXT NOT NULL,
    data_origin TEXT NOT NULL CHECK(data_origin = 'SYNTHETIC'),
    actor_id TEXT NOT NULL,
    registered_at_utc TEXT NOT NULL
);
CREATE TABLE sell_quotation (
    id TEXT PRIMARY KEY,
    rfq_id TEXT NOT NULL REFERENCES sell_rfq(id),
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
CREATE TABLE sell_quotation_revision (
    quotation_id TEXT NOT NULL REFERENCES sell_quotation(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    rfq_id TEXT NOT NULL REFERENCES sell_rfq(id),
    rfq_revision INTEGER NOT NULL,
    price_authority_id TEXT NOT NULL REFERENCES sell_price_authority(id),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY(quotation_id, revision),
    FOREIGN KEY(rfq_id, rfq_revision) REFERENCES sell_rfq_revision(rfq_id, revision)
);
CREATE TABLE sell_quotation_decision (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    quotation_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('APPROVE', 'REVOKE')),
    payload_sha256 TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    decided_at_utc TEXT NOT NULL,
    FOREIGN KEY(quotation_id, revision)
        REFERENCES sell_quotation_revision(quotation_id, revision)
);
CREATE TRIGGER sell_price_authority_no_update BEFORE UPDATE ON sell_price_authority
BEGIN SELECT RAISE(ABORT, 'price authority is immutable'); END;
CREATE TRIGGER sell_price_authority_no_delete BEFORE DELETE ON sell_price_authority
BEGIN SELECT RAISE(ABORT, 'price authority is immutable'); END;
CREATE TRIGGER sell_quotation_no_update BEFORE UPDATE ON sell_quotation
BEGIN SELECT RAISE(ABORT, 'quotation identity is immutable'); END;
CREATE TRIGGER sell_quotation_no_delete BEFORE DELETE ON sell_quotation
BEGIN SELECT RAISE(ABORT, 'quotation identity is immutable'); END;
CREATE TRIGGER sell_quotation_revision_no_update BEFORE UPDATE ON sell_quotation_revision
BEGIN SELECT RAISE(ABORT, 'quotation revisions are append-only'); END;
CREATE TRIGGER sell_quotation_revision_no_delete BEFORE DELETE ON sell_quotation_revision
BEGIN SELECT RAISE(ABORT, 'quotation revisions are append-only'); END;
CREATE TRIGGER sell_quotation_decision_no_update BEFORE UPDATE ON sell_quotation_decision
BEGIN SELECT RAISE(ABORT, 'quotation decisions are append-only'); END;
CREATE TRIGGER sell_quotation_decision_no_delete BEFORE DELETE ON sell_quotation_decision
BEGIN SELECT RAISE(ABORT, 'quotation decisions are append-only'); END;

PRAGMA user_version = 20;
COMMIT;
