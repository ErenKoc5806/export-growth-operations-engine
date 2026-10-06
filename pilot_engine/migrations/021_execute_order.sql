BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS execute_po (
    id TEXT PRIMARY KEY,
    quotation_id TEXT NOT NULL REFERENCES sell_quotation(id),
    source_ref TEXT NOT NULL UNIQUE,
    original_ref TEXT NOT NULL,
    original_sha256 TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    data_origin TEXT NOT NULL CHECK(data_origin = 'SYNTHETIC'),
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execute_po_revision (
    po_id TEXT NOT NULL REFERENCES execute_po(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    quotation_revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY(po_id, revision)
);
CREATE TABLE IF NOT EXISTS execute_po_decision (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('APPROVE', 'REVOKE')),
    payload_sha256 TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    decided_at_utc TEXT NOT NULL,
    FOREIGN KEY(po_id, revision) REFERENCES execute_po_revision(po_id, revision)
);
CREATE TABLE IF NOT EXISTS execute_local_order (
    id TEXT PRIMARY KEY,
    po_id TEXT NOT NULL UNIQUE REFERENCES execute_po(id),
    po_revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY(po_id, po_revision) REFERENCES execute_po_revision(po_id, revision)
);
CREATE TRIGGER IF NOT EXISTS execute_po_no_update BEFORE UPDATE ON execute_po
BEGIN SELECT RAISE(ABORT, 'PO identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS execute_po_revision_no_update BEFORE UPDATE ON execute_po_revision
BEGIN SELECT RAISE(ABORT, 'PO revisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_po_decision_no_update BEFORE UPDATE ON execute_po_decision
BEGIN SELECT RAISE(ABORT, 'PO decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_order_no_update BEFORE UPDATE ON execute_local_order
BEGIN SELECT RAISE(ABORT, 'local order is immutable'); END;
CREATE TRIGGER IF NOT EXISTS execute_po_no_delete BEFORE DELETE ON execute_po
BEGIN SELECT RAISE(ABORT, 'PO identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS execute_po_revision_no_delete BEFORE DELETE ON execute_po_revision
BEGIN SELECT RAISE(ABORT, 'PO revisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_po_decision_no_delete BEFORE DELETE ON execute_po_decision
BEGIN SELECT RAISE(ABORT, 'PO decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_order_no_delete BEFORE DELETE ON execute_local_order
BEGIN SELECT RAISE(ABORT, 'local order is immutable'); END;

PRAGMA user_version = 21;
COMMIT;
