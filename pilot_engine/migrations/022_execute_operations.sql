BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS execute_operation_event (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_key TEXT NOT NULL UNIQUE,
    order_id TEXT NOT NULL REFERENCES execute_local_order(id),
    kind TEXT NOT NULL CHECK(kind IN ('MANUAL_ERP_HANDOFF', 'READINESS',
                                      'FREIGHT_PLAN', 'BOOKING_CONFIRMATION')),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS execute_operation_latest ON execute_operation_event(order_id, kind, sequence DESC);
CREATE TRIGGER IF NOT EXISTS execute_operation_no_update BEFORE UPDATE ON execute_operation_event
BEGIN SELECT RAISE(ABORT, 'execute events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS execute_operation_no_delete BEFORE DELETE ON execute_operation_event
BEGIN SELECT RAISE(ABORT, 'execute events are append-only'); END;

PRAGMA user_version = 22;
COMMIT;
