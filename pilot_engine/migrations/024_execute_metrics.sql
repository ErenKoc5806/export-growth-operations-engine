BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS execute_case_metric (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    order_id TEXT NOT NULL REFERENCES execute_local_order(id),
    minutes INTEGER NOT NULL CHECK(minutes >= 0),
    corrections INTEGER NOT NULL CHECK(corrections >= 0),
    failures INTEGER NOT NULL CHECK(failures >= 0),
    evidence_ref TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS execute_case_metric_no_update BEFORE UPDATE ON execute_case_metric
BEGIN SELECT RAISE(ABORT, 'case metrics are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_case_metric_no_delete BEFORE DELETE ON execute_case_metric
BEGIN SELECT RAISE(ABORT, 'case metrics are append-only'); END;

PRAGMA user_version = 24;
COMMIT;
