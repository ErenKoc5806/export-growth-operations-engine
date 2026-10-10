BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS execute_export_fact (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES execute_local_order(id),
    order_sha256 TEXT NOT NULL,
    hs_code TEXT NOT NULL,
    classification_evidence_ref TEXT NOT NULL,
    origin_country_code TEXT NOT NULL,
    origin_evidence_ref TEXT NOT NULL,
    manufacturer_review_ref TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    reviewed_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS execute_export_fact_latest ON execute_export_fact(order_id, sequence DESC);
CREATE TRIGGER IF NOT EXISTS execute_export_fact_no_update BEFORE UPDATE ON execute_export_fact
BEGIN SELECT RAISE(ABORT, 'export facts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_export_fact_no_delete BEFORE DELETE ON execute_export_fact
BEGIN SELECT RAISE(ABORT, 'export facts are append-only'); END;

PRAGMA user_version = 26;
COMMIT;
