BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS execute_po_amendment (
    order_id TEXT PRIMARY KEY REFERENCES execute_local_order(id),
    po_id TEXT NOT NULL,
    po_revision INTEGER NOT NULL,
    quotation_revision INTEGER NOT NULL,
    original_sha256 TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    differences_json TEXT NOT NULL CHECK(json_valid(differences_json)),
    reason TEXT NOT NULL,
    decision_sequence INTEGER NOT NULL UNIQUE REFERENCES execute_po_decision(sequence),
    actor_id TEXT NOT NULL,
    approved_at_utc TEXT NOT NULL,
    FOREIGN KEY(po_id, po_revision) REFERENCES execute_po_revision(po_id, revision)
);
CREATE TRIGGER IF NOT EXISTS execute_po_amendment_no_update BEFORE UPDATE ON execute_po_amendment
BEGIN SELECT RAISE(ABORT, 'PO amendments are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_po_amendment_no_delete BEFORE DELETE ON execute_po_amendment
BEGIN SELECT RAISE(ABORT, 'PO amendments are append-only'); END;

PRAGMA user_version = 27;
COMMIT;
