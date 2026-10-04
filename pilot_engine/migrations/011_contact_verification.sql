BEGIN IMMEDIATE;

CREATE TABLE contact_route_check (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id TEXT NOT NULL REFERENCES discovered_contact_route(id),
    observation_sha256 TEXT NOT NULL,
    policy_sequence INTEGER NOT NULL REFERENCES contact_collection_policy(sequence),
    method TEXT NOT NULL CHECK(method IN ('MANUAL_PAGE', 'MANUAL_CALL', 'PROVIDER_FEEDBACK')),
    result TEXT NOT NULL CHECK(result IN ('ROUTE_CONFIRMED', 'UNCERTAIN', 'INVALID', 'BOUNCED')),
    source_url TEXT NOT NULL,
    checked_at_utc TEXT NOT NULL,
    explanation TEXT NOT NULL,
    domain_review_ref TEXT,
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);
CREATE INDEX contact_check_latest ON contact_route_check(route_id, sequence DESC);
CREATE TRIGGER contact_check_no_update BEFORE UPDATE ON contact_route_check
BEGIN SELECT RAISE(ABORT, 'contact checks are append-only'); END;
CREATE TRIGGER contact_check_no_delete BEFORE DELETE ON contact_route_check
BEGIN SELECT RAISE(ABORT, 'contact checks are append-only'); END;

PRAGMA user_version = 11;
COMMIT;
