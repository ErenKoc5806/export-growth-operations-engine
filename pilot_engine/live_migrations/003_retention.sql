BEGIN IMMEDIATE;

CREATE TABLE live_retention_binding (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    database_id TEXT NOT NULL,
    ledger_id TEXT NOT NULL
);
-- Minimal receipts survive retirement; they contain no source/payload text.
-- Deliberately no FK: a retired pilot's operational rows are removed.
CREATE TABLE live_document_purge (
    document_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    purged_at_utc TEXT NOT NULL
);

COMMIT;
