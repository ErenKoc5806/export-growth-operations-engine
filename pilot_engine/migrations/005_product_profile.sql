BEGIN IMMEDIATE;

-- Profiles are independent of synthetic case ingestion. A revision is a full
-- snapshot; an approval is bound to that exact snapshot, not a mutable row.
CREATE TABLE product_profile_revision (
    product_id TEXT NOT NULL REFERENCES product(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY(product_id, revision)
);
CREATE TABLE product_profile_event (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('APPROVED', 'REVOKED')),
    actor_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    occurred_at_utc TEXT NOT NULL,
    FOREIGN KEY(product_id, revision) REFERENCES product_profile_revision(product_id, revision)
);
CREATE TABLE profile_revalidation (
    opportunity_id TEXT PRIMARY KEY REFERENCES opportunity(id),
    required_revision INTEGER NOT NULL,
    reviewed_revision INTEGER NOT NULL DEFAULT 0,
    reviewed_by TEXT,
    reviewed_at_utc TEXT
);
CREATE TRIGGER profile_new_opportunity_review AFTER INSERT ON opportunity
WHEN EXISTS (SELECT 1 FROM product_profile_revision WHERE product_id = NEW.product_id)
BEGIN
    INSERT INTO profile_revalidation (opportunity_id, required_revision, reviewed_revision)
    SELECT NEW.id, MAX(revision), 0 FROM product_profile_revision
    WHERE product_id = NEW.product_id;
END;
CREATE TRIGGER profile_revision_no_update BEFORE UPDATE ON product_profile_revision
BEGIN SELECT RAISE(ABORT, 'profile revisions are append-only'); END;
CREATE TRIGGER profile_revision_no_delete BEFORE DELETE ON product_profile_revision
BEGIN SELECT RAISE(ABORT, 'profile revisions are append-only'); END;
CREATE TRIGGER profile_event_no_update BEFORE UPDATE ON product_profile_event
BEGIN SELECT RAISE(ABORT, 'profile decisions are append-only'); END;
CREATE TRIGGER profile_event_no_delete BEFORE DELETE ON product_profile_event
BEGIN SELECT RAISE(ABORT, 'profile decisions are append-only'); END;

PRAGMA user_version = 5;
COMMIT;
