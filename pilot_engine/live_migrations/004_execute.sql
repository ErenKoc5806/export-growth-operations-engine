BEGIN IMMEDIATE;
CREATE TABLE live_order (
    id TEXT PRIMARY KEY CHECK(id LIKE 'LIVE-ORDER-%'),
    pilot_id TEXT NOT NULL REFERENCES live_pilot(id),
    po_intake_id TEXT NOT NULL UNIQUE REFERENCES live_intake(id),
    po_number_key TEXT NOT NULL, buyer_key TEXT NOT NULL,
    actor_id TEXT NOT NULL, recorded_at_utc TEXT NOT NULL,
    UNIQUE(pilot_id,buyer_key,po_number_key)
);
CREATE TABLE live_order_revision (
    order_id TEXT NOT NULL REFERENCES live_order(id), revision INTEGER NOT NULL CHECK(revision>0),
    facts_json TEXT NOT NULL CHECK(json_valid(facts_json)),
    bundle_json TEXT NOT NULL CHECK(json_valid(bundle_json)),
    bundle_sha256 TEXT NOT NULL CHECK(length(bundle_sha256)=64),
    document_id TEXT NOT NULL REFERENCES live_document(id),
    permission_document_id TEXT NOT NULL REFERENCES live_document(id),
    actor_id TEXT NOT NULL, recorded_at_utc TEXT NOT NULL,
    PRIMARY KEY(order_id,revision), UNIQUE(order_id,revision,bundle_sha256)
);
CREATE TABLE live_order_input (
    order_id TEXT NOT NULL, order_revision INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('PRODUCT_PROFILE','PRICE_AUTHORITY','CUSTOMER_PO')),
    intake_id TEXT NOT NULL, intake_revision INTEGER NOT NULL, payload_sha256 TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES live_source(id),
    confirmation_sequence INTEGER NOT NULL REFERENCES live_confirmation(sequence),
    PRIMARY KEY(order_id,order_revision,kind),
    FOREIGN KEY(order_id,order_revision) REFERENCES live_order_revision(order_id,revision),
    FOREIGN KEY(intake_id,intake_revision,payload_sha256)
        REFERENCES live_intake_revision(intake_id,revision,payload_sha256)
);
CREATE TABLE live_order_review (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL, revision INTEGER NOT NULL, bundle_sha256 TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('APPROVE','REJECT','REVOKE')),
    approver_name TEXT NOT NULL, approver_role TEXT NOT NULL, authority_ref TEXT NOT NULL,
    reason TEXT NOT NULL, method TEXT NOT NULL, evidence_ref TEXT NOT NULL,
    confirmed_at_utc TEXT NOT NULL, actor_id TEXT NOT NULL, recorded_at_utc TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES live_document(id),
    identity_basis TEXT NOT NULL CHECK(identity_basis='OPERATOR_ATTESTATION'),
    FOREIGN KEY(order_id,revision,bundle_sha256)
        REFERENCES live_order_revision(order_id,revision,bundle_sha256)
);
CREATE TABLE live_order_measurement (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL, revision INTEGER NOT NULL, bundle_sha256 TEXT NOT NULL,
    review_sequence INTEGER NOT NULL REFERENCES live_order_review(sequence),
    baseline_minutes TEXT NOT NULL, elapsed_minutes TEXT NOT NULL,
    corrections INTEGER NOT NULL CHECK(corrections>=0), notes TEXT NOT NULL,
    baseline_ref TEXT NOT NULL, actor_id TEXT NOT NULL, recorded_at_utc TEXT NOT NULL,
    UNIQUE(order_id,revision,review_sequence),
    FOREIGN KEY(order_id,revision,bundle_sha256)
        REFERENCES live_order_revision(order_id,revision,bundle_sha256)
);
CREATE TABLE live_order_export (
    id TEXT PRIMARY KEY CHECK(id LIKE 'LIVE-EXPORT-%'),
    order_id TEXT NOT NULL, revision INTEGER NOT NULL, bundle_sha256 TEXT NOT NULL,
    review_sequence INTEGER NOT NULL REFERENCES live_order_review(sequence),
    copy_id TEXT NOT NULL UNIQUE, artifact_sha256 TEXT NOT NULL CHECK(length(artifact_sha256)=64),
    actor_id TEXT NOT NULL, recorded_at_utc TEXT NOT NULL,
    FOREIGN KEY(order_id,revision,bundle_sha256)
        REFERENCES live_order_revision(order_id,revision,bundle_sha256)
);
COMMIT;
