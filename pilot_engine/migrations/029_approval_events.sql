BEGIN IMMEDIATE;

-- The original approval ID encoded action/target/revision and could not hold a
-- second decision. Preserve it for legacy outreach foreign keys, while new
-- quote/PO decisions get individual IDs and a database-ordered sequence.
CREATE TABLE IF NOT EXISTS approval_event (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    opportunity_id TEXT NOT NULL REFERENCES opportunity(id),
    action TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_revision INTEGER,
    content_hash TEXT,
    actor_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('APPROVED', 'REVOKED', 'REJECTED')),
    reason TEXT NOT NULL,
    decided_at_utc TEXT NOT NULL,
    legacy_approval_id TEXT UNIQUE REFERENCES approval(id)
);
INSERT OR IGNORE INTO approval_event (id, opportunity_id, action, target_id,
    target_revision, content_hash, actor_id, decision, reason,
    decided_at_utc, legacy_approval_id)
SELECT id, opportunity_id, action, target_id, target_revision, content_hash,
       actor_id, decision, 'Migrated legacy approval', decided_at_utc, id
FROM approval ORDER BY rowid;
CREATE INDEX IF NOT EXISTS approval_event_target ON approval_event
    (opportunity_id, action, target_id, target_revision, sequence DESC);
CREATE TRIGGER IF NOT EXISTS approval_event_no_update BEFORE UPDATE ON approval_event
BEGIN SELECT RAISE(ABORT, 'approval events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS approval_event_no_delete BEFORE DELETE ON approval_event
BEGIN SELECT RAISE(ABORT, 'approval events are append-only'); END;

PRAGMA user_version = 29;
COMMIT;
