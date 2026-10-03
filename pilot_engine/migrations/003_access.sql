BEGIN IMMEDIATE;
CREATE TABLE access_decision (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id TEXT NOT NULL, permission TEXT NOT NULL, target_id TEXT NOT NULL,
    allowed INTEGER NOT NULL CHECK(allowed IN (0, 1)),
    occurred_at_utc TEXT NOT NULL
);
CREATE TRIGGER access_no_update BEFORE UPDATE ON access_decision
BEGIN SELECT RAISE(ABORT, 'access decisions are append-only'); END;
CREATE TRIGGER access_no_delete BEFORE DELETE ON access_decision
BEGIN SELECT RAISE(ABORT, 'access decisions are append-only'); END;
PRAGMA user_version = 3;
COMMIT;
