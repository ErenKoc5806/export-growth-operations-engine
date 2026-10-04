BEGIN IMMEDIATE;

-- Keep only the digest and operator-supplied reference; original documents live
-- in the controlled document store, outside this pilot database.
CREATE TABLE profile_source_document (
    source_ref TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    byte_length INTEGER NOT NULL CHECK(byte_length > 0),
    actor_id TEXT NOT NULL,
    registered_at_utc TEXT NOT NULL
);
CREATE TRIGGER profile_source_no_update BEFORE UPDATE ON profile_source_document
BEGIN SELECT RAISE(ABORT, 'profile sources are append-only'); END;
CREATE TRIGGER profile_source_no_delete BEFORE DELETE ON profile_source_document
BEGIN SELECT RAISE(ABORT, 'profile sources are append-only'); END;

PRAGMA user_version = 9;
COMMIT;
