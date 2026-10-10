BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS execute_document (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES execute_local_order(id),
    kind TEXT NOT NULL CHECK(kind IN ('INVOICE', 'PACKING', 'CHECKLIST')),
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE(order_id, kind)
);
CREATE TABLE IF NOT EXISTS execute_document_revision (
    document_id TEXT NOT NULL REFERENCES execute_document(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    order_sha256 TEXT NOT NULL,
    freight_sequence INTEGER REFERENCES execute_operation_event(sequence),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY(document_id, revision)
);
CREATE TABLE IF NOT EXISTS execute_document_decision (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('REVIEW', 'REVOKE')),
    payload_sha256 TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    reviewed_at_utc TEXT NOT NULL,
    FOREIGN KEY(document_id, revision) REFERENCES execute_document_revision(document_id, revision)
);
CREATE TRIGGER IF NOT EXISTS execute_document_no_update BEFORE UPDATE ON execute_document
BEGIN SELECT RAISE(ABORT, 'execute document identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS execute_document_no_delete BEFORE DELETE ON execute_document
BEGIN SELECT RAISE(ABORT, 'execute document identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS execute_document_revision_no_update BEFORE UPDATE ON execute_document_revision
BEGIN SELECT RAISE(ABORT, 'execute documents are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_document_revision_no_delete BEFORE DELETE ON execute_document_revision
BEGIN SELECT RAISE(ABORT, 'execute documents are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_document_decision_no_update BEFORE UPDATE ON execute_document_decision
BEGIN SELECT RAISE(ABORT, 'execute decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_document_decision_no_delete BEFORE DELETE ON execute_document_decision
BEGIN SELECT RAISE(ABORT, 'execute decisions are append-only'); END;

PRAGMA user_version = 23;
COMMIT;
