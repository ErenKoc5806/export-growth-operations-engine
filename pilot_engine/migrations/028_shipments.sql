BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS execute_shipment (
    id TEXT PRIMARY KEY,
    operation_key TEXT NOT NULL UNIQUE,
    order_id TEXT NOT NULL REFERENCES execute_local_order(id),
    order_sha256 TEXT NOT NULL,
    quantity TEXT NOT NULL,
    source_ref TEXT NOT NULL CHECK(source_ref LIKE 'SYN-%'),
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS execute_shipment_order ON execute_shipment(order_id);
CREATE TABLE IF NOT EXISTS execute_shipment_freight (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_key TEXT NOT NULL UNIQUE,
    shipment_id TEXT NOT NULL REFERENCES execute_shipment(id),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS execute_shipment_freight_latest ON execute_shipment_freight(shipment_id, sequence DESC);
CREATE TABLE IF NOT EXISTS execute_shipment_document (
    id TEXT PRIMARY KEY,
    shipment_id TEXT NOT NULL REFERENCES execute_shipment(id),
    kind TEXT NOT NULL CHECK(kind IN ('PACKING','INVOICE')),
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE(shipment_id, kind)
);
CREATE TABLE IF NOT EXISTS execute_shipment_document_revision (
    document_id TEXT NOT NULL REFERENCES execute_shipment_document(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    order_sha256 TEXT NOT NULL,
    freight_sequence INTEGER REFERENCES execute_shipment_freight(sequence),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY(document_id, revision)
);
CREATE TABLE IF NOT EXISTS execute_shipment_document_decision (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('REVIEW','REVOKE')),
    payload_sha256 TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    reviewed_at_utc TEXT NOT NULL,
    FOREIGN KEY(document_id, revision) REFERENCES execute_shipment_document_revision(document_id, revision)
);
CREATE TABLE IF NOT EXISTS execute_shipment_booking (
    shipment_id TEXT PRIMARY KEY REFERENCES execute_shipment(id),
    operation_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS execute_shipment_no_update BEFORE UPDATE ON execute_shipment
BEGIN SELECT RAISE(ABORT, 'shipments are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_shipment_no_delete BEFORE DELETE ON execute_shipment
BEGIN SELECT RAISE(ABORT, 'shipments are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_shipment_freight_no_update BEFORE UPDATE ON execute_shipment_freight
BEGIN SELECT RAISE(ABORT, 'shipment freight is append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_shipment_freight_no_delete BEFORE DELETE ON execute_shipment_freight
BEGIN SELECT RAISE(ABORT, 'shipment freight is append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_shipment_document_no_update BEFORE UPDATE ON execute_shipment_document
BEGIN SELECT RAISE(ABORT, 'shipment documents are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_shipment_document_no_delete BEFORE DELETE ON execute_shipment_document
BEGIN SELECT RAISE(ABORT, 'shipment documents are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_shipment_revision_no_update BEFORE UPDATE ON execute_shipment_document_revision
BEGIN SELECT RAISE(ABORT, 'shipment revisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_shipment_revision_no_delete BEFORE DELETE ON execute_shipment_document_revision
BEGIN SELECT RAISE(ABORT, 'shipment revisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_shipment_decision_no_update BEFORE UPDATE ON execute_shipment_document_decision
BEGIN SELECT RAISE(ABORT, 'shipment decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_shipment_decision_no_delete BEFORE DELETE ON execute_shipment_document_decision
BEGIN SELECT RAISE(ABORT, 'shipment decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_shipment_booking_no_update BEFORE UPDATE ON execute_shipment_booking
BEGIN SELECT RAISE(ABORT, 'shipment booking is append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_shipment_booking_no_delete BEFORE DELETE ON execute_shipment_booking
BEGIN SELECT RAISE(ABORT, 'shipment booking is append-only'); END;

PRAGMA user_version = 28;
COMMIT;
